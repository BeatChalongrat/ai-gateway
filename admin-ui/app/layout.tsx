import "./globals.css";
import { Outfit } from "next/font/google";
import { ToastProvider } from "../components/ui/Toast";
import ShellWrapper from "../components/ShellWrapper";
import { ThemeProvider } from "../components/ThemeProvider";
import { I18nProvider } from "../lib/i18n";

const outfit = Outfit({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-outfit",
  display: "swap",
});

export const metadata = {
  title: "SRAN AI Gateway",
  description: "SRAN AI Gateway Admin Console",
  icons: {
    icon: "/sran-logo.png",
  },
};

// Runs synchronously before React hydrates so data-theme is set before
// first paint. Prevents a flash of wrong-theme on initial load.
const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem("sran-theme")||"dark";document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={outfit.variable} data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>
          {/* Outside ToastProvider so toast copy can be translated too. */}
          <I18nProvider>
            <ToastProvider>
              <ShellWrapper>{children}</ShellWrapper>
            </ToastProvider>
          </I18nProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
