import "./globals.css";

export const metadata = {
  title: "Minimal Blog",
  description: "Stage 1 — local; later S3 + CloudFront",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
