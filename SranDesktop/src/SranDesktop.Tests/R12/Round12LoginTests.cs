using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.R12;

public class LoginUrlDerivationTests
{
    [Theory]
    // Local split-port dev: portal runs on :3001, not on the gateway's :8080.
    [InlineData("http://localhost:8080", "http://localhost:3001")]
    [InlineData("http://127.0.0.1:8080", "http://localhost:3001")]
    // Production single-URL mode: nginx serves the Portal at the root path.
    [InlineData("https://sran.yourcompany.com", "https://sran.yourcompany.com")]
    [InlineData("https://sran.yourcompany.com/", "https://sran.yourcompany.com")]
    public void Portal_Web_Url_Is_Derived_From_Gateway_Url(string gatewayUrl, string expected)
    {
        Assert.Equal(expected, LoginViewModel.DerivePortalWebUrl(gatewayUrl));
    }
}

public class LoginDictTests
{
    // The simplified login screen's full key set (R12-fix).
    [Theory]
    [InlineData("login.email")]
    [InlineData("login.emailPlaceholder")]
    [InlineData("login.password")]
    [InlineData("login.signIn")]
    [InlineData("login.signingIn")]
    [InlineData("login.forgotPassword")]
    [InlineData("login.showPassword")]
    [InlineData("login.toggleTheme")]
    [InlineData("login.errorInvalid")]
    [InlineData("login.errorUnreachable")]
    [InlineData("login.errorTimeout")]
    public void Login_Keys_Exist_In_Both_Locales(string key)
    {
        Assert.True(Dict.En.ContainsKey(key), $"EN missing {key}");
        Assert.True(Dict.Th.ContainsKey(key), $"TH missing {key}");
    }

    [Theory]
    // Removed with the simplified layout — must not linger in the dictionaries.
    [InlineData("login.brandTagline")]
    [InlineData("login.subtitle")]
    [InlineData("login.orContinueWith")]
    [InlineData("login.noAccount")]
    [InlineData("login.signUp")]
    [InlineData("login.connectedTo")]
    [InlineData("login.changeServer")]
    [InlineData("login.feature1")]
    [InlineData("login.feature2")]
    [InlineData("login.feature3")]
    public void Removed_Login_Keys_Are_Gone(string key)
    {
        Assert.False(Dict.En.ContainsKey(key), $"EN still has {key}");
        Assert.False(Dict.Th.ContainsKey(key), $"TH still has {key}");
    }
}
