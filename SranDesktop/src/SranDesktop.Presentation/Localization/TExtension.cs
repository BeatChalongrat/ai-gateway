using System.Windows.Data;
using System.Windows.Markup;

namespace SranDesktop.Presentation.Localization;

/// <summary>
/// Localized-string markup extension: <c>Text="{loc:T sidebar.newChat}"</c>.
/// Produces a OneWay binding into <see cref="LocaleService.ActiveDict"/> so the
/// text live-updates when the locale switches.
/// </summary>
[MarkupExtensionReturnType(typeof(string))]
public sealed class TExtension : MarkupExtension
{
    public string Key { get; set; }

    public TExtension(string key) => Key = key;

    public override object ProvideValue(IServiceProvider serviceProvider)
    {
        var binding = new Binding($"{nameof(LocaleService.ActiveDict)}[{Key}]")
        {
            Source = LocaleService.Instance,
            FallbackValue = Key,
            Mode = BindingMode.OneWay
        };
        return binding.ProvideValue(serviceProvider);
    }
}
