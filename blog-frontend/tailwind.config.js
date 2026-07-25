/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0f1115",
        surface: "#171a21",
        surface2: "#1e222b",
        line: "#2a2f3a",
        ink: "#e6e9ef",
        muted: "#9aa4b2",
        accent: "#6ea8fe",
        accent2: "#8b7cf6",
        ok: "#5ad19a",
        warn: "#f0b45e",
        err: "#f47a7a",
        onaccent: "#0b1020",
      },
    },
  },
  plugins: [],
}
