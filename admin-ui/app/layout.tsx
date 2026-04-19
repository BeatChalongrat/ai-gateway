import "./globals.css";
import { Outfit } from "next/font/google";
import { ToastProvider } from "../components/ui/Toast";
import ShellWrapper from "../components/ShellWrapper";

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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={outfit.variable}>
      <body>
        <ToastProvider>
          <ShellWrapper>{children}</ShellWrapper>
        </ToastProvider>
      </body>
    </html>
  );
}
