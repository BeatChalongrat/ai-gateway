using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// One row in the composer's slash menu (Phase 12). Thin display wrapper over
/// the platform-agnostic <see cref="SlashCommand"/>: the labels are resolved
/// through the dictionary here so the command registry stays free of UI
/// concerns and can be unit-tested without the localization service.
/// </summary>
public sealed class SlashCommandItemViewModel
{
    public SlashCommandItemViewModel(SlashCommand command)
    {
        Command = command;
        Label = LocaleService.Instance.T(command.LabelKey);
        Hint = LocaleService.Instance.T(command.HintKey);
        Typed = "/" + command.Keyword;
        Icon = command.Id switch
        {
            SlashActionId.Image => "Geo_ImageBadge",
            SlashActionId.Video => "Geo_VideoBadge",
            SlashActionId.NewChat => "Geo_Plus",
            SlashActionId.Shortcuts => "Geo_Keyboard",
            _ => "Geo_Tool",
        };
    }

    public SlashCommand Command { get; }

    public string Label { get; }

    public string Hint { get; }

    /// <summary>The literal the user types, rendered as a hint chip.</summary>
    public string Typed { get; }

    /// <summary>Geometry resource key for the row icon.</summary>
    public string Icon { get; }
}
