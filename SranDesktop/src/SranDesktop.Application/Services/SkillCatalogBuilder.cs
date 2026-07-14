using System.Text;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>
/// Builds the compact skill catalog appended to the agent system prompt
/// (Phase 6 Task 2), so the model can discover skills that no exact-substring
/// trigger caught (e.g. a Thai paraphrase). One line per ENABLED skill; skills
/// already inlined this turn (trigger fast-path) are marked "(loaded)" so the
/// model doesn't fetch them again via use_skill. Descriptions are capped to
/// keep the catalog cheap — the full SKILL.md only enters context when matched
/// or fetched. Pure + tested.
/// </summary>
public static class SkillCatalogBuilder
{
    private const int MaxDescriptionChars = 120;

    /// <summary>Returns the catalog block, or null when there are no enabled
    /// skills. <paramref name="alreadyLoadedNames"/> are the skills whose
    /// SKILL.md is already inline this turn.</summary>
    public static string? Build(
        IReadOnlyList<Skill> skills,
        IReadOnlyCollection<string> alreadyLoadedNames)
    {
        var enabled = skills.Where(s => s.IsEnabled).ToList();
        if (enabled.Count == 0)
            return null;

        var loaded = new HashSet<string>(alreadyLoadedNames, StringComparer.OrdinalIgnoreCase);

        var sb = new StringBuilder();
        sb.Append("\n\n## Skills\n");
        sb.Append("These skills provide task-specific instructions. When a request matches one, ");
        sb.Append("call the use_skill tool with its name FIRST and follow the returned instructions. ");
        sb.Append("Skills marked (loaded) are already active this turn — do not fetch them again.\n");

        foreach (var skill in enabled)
        {
            var name = skill.Manifest.Name;
            sb.Append("- ").Append(name);
            if (loaded.Contains(name))
                sb.Append(" (loaded)");
            sb.Append(": ").Append(DescriptionOf(skill)).Append('\n');
        }

        return sb.ToString();
    }

    /// <summary>Description for the catalog line: the manifest description,
    /// falling back to the joined trigger list when it's blank, then capped.</summary>
    private static string DescriptionOf(Skill skill)
    {
        var description = skill.Manifest.Description;
        if (string.IsNullOrWhiteSpace(description))
            description = string.Join(", ", skill.Manifest.Triggers.Where(t => t.Length > 0));
        description = description.Replace('\n', ' ').Replace('\r', ' ').Trim();

        if (description.Length > MaxDescriptionChars)
            description = description[..MaxDescriptionChars].TrimEnd() + "…";
        return description;
    }
}
