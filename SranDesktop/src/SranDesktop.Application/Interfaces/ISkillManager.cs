using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>Loads, matches, and manages skill modules (R9 Feature H).</summary>
public interface ISkillManager
{
    /// <summary>All loaded skills (built-in + user-installed).</summary>
    Task<IReadOnlyList<Skill>> GetSkillsAsync(CancellationToken ct = default);

    /// <summary>Enabled skills whose triggers match the message text.</summary>
    Task<IReadOnlyList<Skill>> FindMatchingSkillsAsync(string message, CancellationToken ct = default);

    /// <summary>Install a .skill (zip) file. Returns the loaded skill.</summary>
    Task<Skill> InstallAsync(string skillZipPath, CancellationToken ct = default);

    /// <summary>Create a user skill from raw fields.</summary>
    Task<Skill> CreateAsync(SkillManifest manifest, string instructions, CancellationToken ct = default);

    Task DeleteAsync(Skill skill, CancellationToken ct = default);

    Task SetEnabledAsync(Skill skill, bool enabled, CancellationToken ct = default);
}
