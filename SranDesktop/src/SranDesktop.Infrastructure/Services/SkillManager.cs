using System.IO.Compression;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// File-based skill store (R9 Feature H). Layout:
///   %APPDATA%\SRAN Desktop\skills\builtin\{name}\{manifest.json, SKILL.md}
///   %APPDATA%\SRAN Desktop\skills\user\{name}\...
/// Built-in skills are (re)written on first load if missing. Disabled skills
/// are tracked in skills\disabled.json.
/// </summary>
public sealed class SkillManager : ISkillManager
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    private readonly string _root;
    private readonly ILogger<SkillManager> _logger;
    private List<Skill>? _cache;
    private readonly SemaphoreSlim _lock = new(1, 1);

    public SkillManager(ILogger<SkillManager> logger) : this(DefaultRoot(), logger)
    {
    }

    public SkillManager(string root, ILogger<SkillManager> logger)
    {
        _root = root;
        _logger = logger;
    }

    private static string DefaultRoot() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop", "skills");

    private string BuiltinDir => Path.Combine(_root, "builtin");
    private string UserDir => Path.Combine(_root, "user");
    private string DisabledFile => Path.Combine(_root, "disabled.json");

    public async Task<IReadOnlyList<Skill>> GetSkillsAsync(CancellationToken ct = default)
    {
        await _lock.WaitAsync(ct);
        try
        {
            if (_cache is not null)
                return _cache;

            EnsureBuiltins();
            var disabled = LoadDisabledSet();
            var skills = new List<Skill>();
            foreach (var (dir, builtin) in new[] { (BuiltinDir, true), (UserDir, false) })
            {
                if (!Directory.Exists(dir)) continue;
                foreach (var folder in Directory.GetDirectories(dir))
                {
                    var skill = TryLoad(folder, builtin);
                    if (skill is null) continue;
                    skill.IsEnabled = !disabled.Contains(skill.Manifest.Name);
                    skills.Add(skill);
                }
            }
            _cache = skills;
            return skills;
        }
        finally
        {
            _lock.Release();
        }
    }

    public async Task<IReadOnlyList<Skill>> FindMatchingSkillsAsync(string message, CancellationToken ct = default)
    {
        var skills = await GetSkillsAsync(ct);
        return skills
            .Where(s => s.IsEnabled && s.Manifest.Triggers.Any(t =>
                t.Length > 0 && message.Contains(t, StringComparison.OrdinalIgnoreCase)))
            .ToList();
    }

    public async Task<Skill> InstallAsync(string skillZipPath, CancellationToken ct = default)
    {
        var staging = Path.Combine(Path.GetTempPath(), "SranDesktop", $"skill-{Guid.NewGuid():N}");
        ZipFile.ExtractToDirectory(skillZipPath, staging);

        // manifest may be at the zip root or in a single top-level folder
        var manifestPath = File.Exists(Path.Combine(staging, "manifest.json"))
            ? Path.Combine(staging, "manifest.json")
            : Directory.GetFiles(staging, "manifest.json", SearchOption.AllDirectories).FirstOrDefault()
              ?? throw new InvalidDataException("manifest.json not found in skill package");
        var sourceDir = Path.GetDirectoryName(manifestPath)!;

        var manifest = JsonSerializer.Deserialize<SkillManifest>(
            await File.ReadAllTextAsync(manifestPath, ct))
            ?? throw new InvalidDataException("manifest.json is invalid");
        if (string.IsNullOrWhiteSpace(manifest.Name))
            throw new InvalidDataException("manifest.json is missing \"name\"");

        var target = Path.Combine(UserDir, SafeName(manifest.Name));
        if (Directory.Exists(target))
            Directory.Delete(target, recursive: true);
        Directory.CreateDirectory(UserDir);
        CopyDirectory(sourceDir, target);
        Directory.Delete(staging, recursive: true);

        InvalidateCache();
        return TryLoad(target, builtin: false)
               ?? throw new InvalidDataException("Skill failed to load after install");
    }

    public async Task<Skill> CreateAsync(SkillManifest manifest, string instructions, CancellationToken ct = default)
    {
        var target = Path.Combine(UserDir, SafeName(manifest.Name));
        Directory.CreateDirectory(target);
        await File.WriteAllTextAsync(Path.Combine(target, "manifest.json"),
            JsonSerializer.Serialize(manifest, JsonOptions), ct);
        await File.WriteAllTextAsync(Path.Combine(target, "SKILL.md"), instructions, ct);
        InvalidateCache();
        return TryLoad(target, builtin: false)!;
    }

    public Task DeleteAsync(Skill skill, CancellationToken ct = default)
    {
        if (skill.IsBuiltin)
            throw new InvalidOperationException("Built-in skills cannot be deleted; disable them instead.");
        if (Directory.Exists(skill.Path))
            Directory.Delete(skill.Path, recursive: true);
        InvalidateCache();
        return Task.CompletedTask;
    }

    public Task SetEnabledAsync(Skill skill, bool enabled, CancellationToken ct = default)
    {
        skill.IsEnabled = enabled;
        var disabled = LoadDisabledSet();
        if (enabled) disabled.Remove(skill.Manifest.Name);
        else disabled.Add(skill.Manifest.Name);
        Directory.CreateDirectory(_root);
        Storage.AtomicFile.Write(DisabledFile, JsonSerializer.Serialize(disabled.ToList(), JsonOptions));
        return Task.CompletedTask;
    }

    private void InvalidateCache() => _cache = null;

    private HashSet<string> LoadDisabledSet()
    {
        try
        {
            if (File.Exists(DisabledFile))
                return JsonSerializer.Deserialize<List<string>>(File.ReadAllText(DisabledFile))
                    ?.ToHashSet(StringComparer.OrdinalIgnoreCase) ?? new(StringComparer.OrdinalIgnoreCase);
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            _logger.LogWarning(ex, "disabled.json unreadable — treating all skills as enabled");
        }
        return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    }

    private Skill? TryLoad(string folder, bool builtin)
    {
        try
        {
            var manifestPath = Path.Combine(folder, "manifest.json");
            var skillPath = Path.Combine(folder, "SKILL.md");
            if (!File.Exists(manifestPath) || !File.Exists(skillPath))
                return null;
            var manifest = JsonSerializer.Deserialize<SkillManifest>(File.ReadAllText(manifestPath));
            if (manifest is null || string.IsNullOrWhiteSpace(manifest.Name))
                return null;
            return new Skill
            {
                Manifest = manifest,
                Instructions = File.ReadAllText(skillPath),
                Path = folder,
                IsBuiltin = builtin
            };
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            _logger.LogWarning(ex, "Skipping unreadable skill at {Folder}", folder);
            return null;
        }
    }

    private static string SafeName(string name)
    {
        foreach (var c in Path.GetInvalidFileNameChars())
            name = name.Replace(c, '-');
        return name;
    }

    private static void CopyDirectory(string source, string target)
    {
        Directory.CreateDirectory(target);
        foreach (var file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source, file);
            var dest = Path.Combine(target, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.Copy(file, dest, overwrite: true);
        }
    }

    // ── Built-in skills (H5) — written to disk if missing ───────────────────

    private void EnsureBuiltins()
    {
        foreach (var (manifest, instructions) in BuiltinSkills())
        {
            var folder = Path.Combine(BuiltinDir, SafeName(manifest.Name));
            var manifestPath = Path.Combine(folder, "manifest.json");
            if (File.Exists(manifestPath))
                continue;
            try
            {
                Directory.CreateDirectory(folder);
                File.WriteAllText(manifestPath, JsonSerializer.Serialize(manifest, JsonOptions));
                File.WriteAllText(Path.Combine(folder, "SKILL.md"), instructions);
            }
            catch (IOException ex)
            {
                _logger.LogWarning(ex, "Could not write built-in skill {Name}", manifest.Name);
            }
        }
    }

    private static IEnumerable<(SkillManifest Manifest, string Instructions)> BuiltinSkills()
    {
        yield return (new SkillManifest
        {
            Name = "docx",
            DisplayName = "Word Document Creator",
            Description = "Create professional Word documents with structured formatting",
            Triggers = new() { "word doc", ".docx", "docx", "report", "memo", "letter", "เอกสาร word", "รายงาน" },
            Icon = "document",
            Author = "SRAN"
        }, """
        # Word Document Creation
        When the user asks for a Word document, report, memo, or letter, respond with a
        :::doc-plan fenced JSON block (format "docx") describing the document structure:
        title page, headings, paragraphs, bullet lists, tables, page breaks.
        Keep the plan actionable: each action has a "type" (add_title_page, add_heading,
        add_paragraph, add_bullet_list, add_numbered_list, add_table, add_page_break)
        plus its content fields. Write content in the user's language. Set a descriptive
        output_filename ending in .docx.
        """);

        yield return (new SkillManifest
        {
            Name = "xlsx",
            DisplayName = "Excel Spreadsheet Builder",
            Description = "Create and edit Excel workbooks with sheets, tables and charts",
            Triggers = new() { "excel", ".xlsx", "xlsx", "spreadsheet", "ตาราง excel", "สเปรดชีต" },
            Icon = "spreadsheet",
            Author = "SRAN"
        }, """
        # Excel Workbook Creation
        When the user asks for a spreadsheet or Excel work, respond with a :::excel-plan
        fenced JSON block. Actions available: create_sheet, write_cells, write_table,
        create_chart, format_range, set_column_width, merge_cells, auto_filter,
        freeze_panes. Reference cells in A1 notation. Include realistic sample data when
        the user hasn't supplied any. Set output_filename ending in .xlsx.
        """);

        yield return (new SkillManifest
        {
            Name = "pdf",
            DisplayName = "PDF Generator",
            Description = "Generate polished PDF reports and documents",
            Triggers = new() { "pdf", ".pdf", "ไฟล์ pdf" },
            Icon = "document",
            Author = "SRAN"
        }, """
        # PDF Generation
        When the user asks for a PDF, respond with a :::doc-plan fenced JSON block with
        format "pdf". Structure the document with headings, paragraphs, lists and tables.
        Thai text is fully supported. Set output_filename ending in .pdf.
        """);

        yield return (new SkillManifest
        {
            Name = "pptx",
            DisplayName = "Presentation Builder",
            Description = "Create PowerPoint decks with structured slides",
            Triggers = new() { "powerpoint", "pptx", "presentation", "slide", "สไลด์", "พรีเซนเทชัน", "งานนำเสนอ" },
            Icon = "presentation",
            Author = "SRAN"
        }, """
        # Presentation Creation
        When the user asks for slides or a presentation, respond with a :::pptx-plan
        fenced JSON block. Slide types: title, section_divider, content,
        content_with_image, two_column, stats, table, chart, closing. Keep each slide
        focused (max ~6 bullets). Open with a title slide and close with a summary.
        Set output_filename ending in .pptx.
        """);

        yield return (new SkillManifest
        {
            Name = "web-search",
            DisplayName = "Web Research",
            Description = "Search the web and cite sources properly",
            Triggers = new() { "search the web", "look up", "latest news", "current price", "ค้นเว็บ", "ข่าวล่าสุด" },
            Icon = "globe",
            Author = "SRAN"
        }, """
        # Web Research
        When answering with web_search results: run focused queries (retry with refined
        terms if the first search is thin), synthesize across sources rather than quoting
        one, and cite each claim with the source domain in parentheses. End with a short
        source list. Prefer recent results for time-sensitive questions.
        """);
    }
}
