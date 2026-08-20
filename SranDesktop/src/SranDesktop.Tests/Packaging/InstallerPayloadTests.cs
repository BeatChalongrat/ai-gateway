using System.Text.RegularExpressions;
using FluentAssertions;
using Xunit;

namespace SranDesktop.Tests.Packaging;

/// <summary>
/// Guards the PACKAGE, not the build output.
///
/// The test that should have caught the missing KaTeX bundle
/// (MathRenderingTests.Vendored_Bundle_Exists_And_Matches_The_Pinned_Version)
/// looks under AppContext.BaseDirectory — the TEST output directory, which
/// gets every Content item. The installed app gets only what the Inno Setup
/// [Files] section copies, and those two sets had silently diverged since
/// 14 Jul: display math and canvas artifacts rendered as raw source in every
/// installed build while that test stayed green.
///
/// So this sweep reads the app's OWN SOURCE for the directories it opens at
/// runtime (Path.Combine(AppContext.BaseDirectory, "…")) and asserts the
/// installer script ships each one. Adding a new runtime asset folder now
/// fails here until the installer learns about it — the same shape as the
/// DLP tool-policy parity sweep, which fails when either registry gains an
/// unclassified tool.
///
/// Chosen over inspecting a produced installer because it needs no ISCC, no
/// build artifact and no admin rights, so it runs in the normal suite on
/// every machine. The weaker property — it verifies the SCRIPT, not the
/// emitted .exe — is acceptable because the script is the thing that was
/// wrong, and Task 4's post-install file listing covers the emitted package.
/// </summary>
public class InstallerPayloadTests
{
    private static string RepoPath(params string[] parts)
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var candidate = Path.Combine(new[] { dir.FullName }.Concat(parts).ToArray());
            if (File.Exists(candidate) || Directory.Exists(candidate))
                return candidate;
            dir = dir.Parent;
        }

        throw new DirectoryNotFoundException(
            $"{string.Join('/', parts)} not found walking up from {AppContext.BaseDirectory}");
    }

    private static string IssText() =>
        File.ReadAllText(RepoPath("SranDesktop", "build", "SranDesktop.iss"));

    /// <summary>Every `Source:` line in the [Files] section, as (source, destDir).</summary>
    private static List<(string Source, string DestDir)> FileEntries()
    {
        var entries = new List<(string, string)>();
        foreach (var line in IssText().Split('\n'))
        {
            var trimmed = line.Trim();
            if (!trimmed.StartsWith("Source:", StringComparison.OrdinalIgnoreCase))
                continue;
            var source = Regex.Match(trimmed, @"Source:\s*""([^""]+)""").Groups[1].Value;
            var dest = Regex.Match(trimmed, @"DestDir:\s*""([^""]+)""").Groups[1].Value;
            entries.Add((source, dest));
        }
        return entries;
    }

    /// <summary>DestDir "{app}\Assets" -> "Assets"; "{app}" -> "".</summary>
    private static string InstallRelative(string destDir) =>
        destDir.Replace("{app}", "", StringComparison.OrdinalIgnoreCase)
               .Trim('\\', '/');

    /// <summary>
    /// Directories and files the app opens relative to its install location.
    /// Read out of the source so the list cannot drift from reality.
    /// </summary>
    private static HashSet<string> RuntimeReadPaths()
    {
        var srcRoot = RepoPath("SranDesktop", "src");
        var wanted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var pattern = new Regex(@"AppContext\.BaseDirectory\s*,\s*""([^""]+)""");

        foreach (var file in Directory.EnumerateFiles(srcRoot, "*.cs", SearchOption.AllDirectories))
        {
            var normalized = file.Replace('\\', '/');
            // The test project's own path juggling is not app runtime behaviour.
            if (normalized.Contains("/SranDesktop.Tests/", StringComparison.OrdinalIgnoreCase)
                || normalized.Contains("/obj/", StringComparison.OrdinalIgnoreCase)
                || normalized.Contains("/bin/", StringComparison.OrdinalIgnoreCase))
                continue;

            foreach (Match m in pattern.Matches(File.ReadAllText(file)))
                wanted.Add(m.Groups[1].Value);
        }

        return wanted;
    }

    private static bool IsShipped(string required, List<(string Source, string DestDir)> entries)
    {
        var isFile = Path.HasExtension(required);

        foreach (var (source, destDir) in entries)
        {
            var dest = InstallRelative(destDir);

            if (isFile)
            {
                // A file is shipped by an explicit Source, or by a wildcard
                // with the same extension landing in {app}.
                var sourceName = Path.GetFileName(source);
                if (dest.Length == 0
                    && (sourceName.Equals(required, StringComparison.OrdinalIgnoreCase)
                        || (sourceName.StartsWith("*.", StringComparison.Ordinal)
                            && Path.GetExtension(required)
                                   .Equals(Path.GetExtension(sourceName), StringComparison.OrdinalIgnoreCase))))
                    return true;
                continue;
            }

            // A directory is shipped when an entry lands in it (or inside it).
            if (dest.Equals(required, StringComparison.OrdinalIgnoreCase)
                || dest.StartsWith(required + "\\", StringComparison.OrdinalIgnoreCase))
                return true;
        }

        return false;
    }

    [Fact]
    public void Every_Runtime_Read_Path_Is_Shipped_By_The_Installer()
    {
        var entries = FileEntries();
        entries.Should().NotBeEmpty("the [Files] section must be parseable");

        var required = RuntimeReadPaths();
        required.Should().NotBeEmpty(
            "the sweep must actually find the app's AppContext.BaseDirectory reads");

        var missing = required.Where(r => !IsShipped(r, entries)).OrderBy(r => r).ToList();

        missing.Should().BeEmpty(
            "SranDesktop.iss must copy everything the app opens at runtime — a folder the "
            + "app reads but the installer omits is invisible until a user reports raw "
            + "output (KaTeX, 14 Jul - 20 Aug). Missing: {0}",
            string.Join(", ", missing));
    }

    [Fact]
    public void The_WebLibs_Bundle_Is_Shipped()
    {
        // Named explicitly so the failure says WHICH feature dies: display
        // math (KaTeX) and canvas artifacts (React) both load from here
        // through the appassets.sran virtual host.
        var entries = FileEntries();

        IsShipped("Assets", entries).Should().BeTrue(
            "MathBlockControl, ArtifactControl and CanvasWindow all map "
            + "{app}\\Assets\\WebLibs into WebView2; without it the mapping is "
            + "skipped silently and the panes render raw source");
    }

    [Fact]
    public void The_Assets_Entry_Recurses_And_Fails_Loudly_When_Publish_Is_Empty()
    {
        var assets = FileEntries().Single(e => InstallRelative(e.DestDir)
            .Equals("Assets", StringComparison.OrdinalIgnoreCase));
        var line = IssText().Split('\n').Single(l => l.Contains(assets.Source, StringComparison.Ordinal)
            && l.TrimStart().StartsWith("Source:", StringComparison.OrdinalIgnoreCase));

        line.Should().Contain("recursesubdirs",
            "WebLibs has nested katex/ and katex/fonts/ folders");
        line.Should().NotContain("skipifsourcedoesntexist",
            "these files are REQUIRED: if publish did not emit them the installer "
            + "compile must fail rather than produce a quietly broken package");
    }
}
