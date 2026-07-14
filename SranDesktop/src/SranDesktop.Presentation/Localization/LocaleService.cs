using System.ComponentModel;
using System.Globalization;

namespace SranDesktop.Presentation.Localization;

/// <summary>
/// Singleton locale manager. XAML binds through <see cref="TExtension"/> to
/// <see cref="ActiveDict"/>; switching <see cref="Locale"/> re-raises the
/// ActiveDict change so every bound string refreshes live.
/// Persistence is delegated to callers via <see cref="LocaleChanged"/>
/// (MainViewModel saves it into UserSettings) — this class stays storage-free
/// so the markup extension can use it before DI is up.
/// </summary>
public sealed class LocaleService : INotifyPropertyChanged
{
    private static readonly Lazy<LocaleService> _instance = new(() => new LocaleService());
    public static LocaleService Instance => _instance.Value;

    private string _locale;

    public event PropertyChangedEventHandler? PropertyChanged;

    /// <summary>Raised after the locale changes (new value) — hook persistence here.</summary>
    public event EventHandler<string>? LocaleChanged;

    private LocaleService()
    {
        _locale = DetectLocale();
    }

    /// <summary>"en" or "th".</summary>
    public string Locale
    {
        get => _locale;
        set
        {
            var normalized = value == "th" ? "th" : "en";
            if (_locale == normalized)
                return;
            _locale = normalized;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Locale)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(LocaleUpper)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(ActiveDict)));
            LocaleChanged?.Invoke(this, normalized);
        }
    }

    /// <summary>"EN" / "TH" badge text for the language toggle row.</summary>
    public string LocaleUpper => _locale.ToUpperInvariant();

    public IReadOnlyDictionary<string, string> ActiveDict =>
        _locale == "th" ? Dict.Th : Dict.En;

    /// <summary>Translate a key; falls back to English, then to the key itself.</summary>
    public string T(string key, params object[] args)
    {
        if (!ActiveDict.TryGetValue(key, out var template)
            && !Dict.En.TryGetValue(key, out template))
            return key;
        return args.Length > 0 ? string.Format(template, args) : template;
    }

    /// <summary>Apply a saved locale without treating null/empty as a change.</summary>
    public void Initialize(string? savedLocale)
    {
        if (!string.IsNullOrEmpty(savedLocale))
            Locale = savedLocale!;
    }

    private static string DetectLocale() =>
        CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "th" ? "th" : "en";
}
