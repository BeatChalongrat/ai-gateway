import "./globals.css";
import { ToastProvider } from "../components/ui/Toast";
import ShellWrapper from "../components/ShellWrapper";

export const metadata = {
  title: "SRAN AI Gateway",
  description: "SRAN AI Gateway Admin Console",
  icons: {
    icon: "/sran-logo.png",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ToastProvider>
          <ShellWrapper>{children}</ShellWrapper>
        </ToastProvider>
      </body>
    </html>
  );
}
