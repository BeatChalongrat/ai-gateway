using System.IO;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;
using SranDesktop.App.Services;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Hosting;
using SranDesktop.Presentation.Services;
using SranDesktop.Presentation.ViewModels;
using SranDesktop.Presentation.Views;
using SranDesktop.Presentation.Views.Dialogs;

namespace SranDesktop.App.Hosting;

/// <summary>WPF-only composition over the shared <see cref="SranCoreServiceCollectionExtensions.AddSranCore"/>.</summary>
public static class AppHostBuilder
{
    public static IHost Build(string[] args)
    {
        return Host.CreateDefaultBuilder(args)
            .UseContentRoot(AppContext.BaseDirectory)
            // Fail a missing host-supplied seam during composition.
            .UseDefaultServiceProvider(o => o.ValidateOnBuild = true)
            .UseSerilog((context, loggerConfiguration) =>
            {
                var logDir = Path.Combine(Environment.GetFolderPath(
                    Environment.SpecialFolder.ApplicationData), "SRAN Desktop", "logs");
                Directory.CreateDirectory(logDir);
                loggerConfiguration.MinimumLevel.Debug().WriteTo.File(
                    Path.Combine(logDir, "app-.log"), rollingInterval: RollingInterval.Day,
                    retainedFileCountLimit: 7);
            })
            .ConfigureServices((context, services) =>
            {
                services.AddSranCore(context.Configuration);
                services.AddSranWindows();
                services.AddSranWindowsDesktop();

                services.AddSingleton<IDialogService, WpfDialogService>();
                // One notifier instance backs both tray contracts.
                services.AddSingleton<TrayCompletionNotifier>();
                services.AddSingleton<ICompletionNotifier>(sp =>
                    sp.GetRequiredService<TrayCompletionNotifier>());
                services.AddSingleton<IWindowStateProvider>(sp =>
                    sp.GetRequiredService<TrayCompletionNotifier>());

                services.AddSingleton<LoginViewModel>();
                services.AddSingleton<ConversationListViewModel>();
                services.AddSingleton<ChatViewModel>();
                services.AddSingleton<MainViewModel>();
                services.AddTransient<SettingsViewModel>();
                services.AddTransient<KnowledgeBaseViewModel>();
                services.AddTransient<MemoryViewModel>();
                services.AddTransient<ScheduledTasksViewModel>();
                services.AddTransient<SkillsViewModel>();
                services.AddTransient<ConnectorMarketplaceViewModel>();
                services.AddTransient<WritingStyleViewModel>();

                AddTransientFactory<SettingsView>(services);
                AddTransientFactory<KnowledgeBaseView>(services);
                AddTransientFactory<MemoryView>(services);
                AddTransientFactory<ScheduledTasksView>(services);
                AddTransientFactory<SkillsView>(services);
                AddTransientFactory<ConnectorMarketplaceView>(services);
                AddTransientFactory<WritingStyleView>(services);
                AddTransientFactory<VoiceSettingsDialog>(services);
                services.AddSingleton<Func<ProjectDto, ProjectSettingsDialog>>(sp => project =>
                    new ProjectSettingsDialog(new ProjectSettingsViewModel(
                        sp.GetRequiredService<IChatEdgeApiClient>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<ProjectSettingsViewModel>>(),
                        project)));
                services.AddSingleton<MainWindow>();
            })
            .Build();
    }

    private static void AddTransientFactory<T>(IServiceCollection services) where T : class
    {
        services.AddTransient<T>();
        services.AddSingleton<Func<T>>(sp => () => sp.GetRequiredService<T>());
    }
}
