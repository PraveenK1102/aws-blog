/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#ffffff",
        cream: "#faf9f7",      // subtle off-white
        ink: "#242424",        // near-black text (Medium-ish)
        soft: "#5c5c5c",       // secondary text
        faint: "#9a9a9a",      // tertiary / meta
        line: "#e9e7e3",       // hairline borders
        accent: "#4f46e5",     // indigo — links / AI
        accent2: "#7c3aed",    // violet — AI gradient end
        ok: "#1a8917",         // published green
        warn: "#b7791f",
        err: "#c0392b",
      },
      fontFamily: {
        serif: ['Georgia', 'Cambria', '"Times New Roman"', 'serif'],
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
      },
      maxWidth: { article: '680px', feed: '760px' },
    },
  },
  plugins: [],
}
