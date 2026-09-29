import "./globals.css";
export const metadata = {
  title: "Exam Intelligence — Daily Current Affairs",
  description: "Current affairs, exam practice, archive and personalized learning.",
  manifest: "/manifest.webmanifest",
};
export default function RootLayout({children}:{children:React.ReactNode}) {
  return <html lang="en"><body>{children}</body></html>;
}
