/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      }
    },
  },
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      {
        light: {
          ...require("daisyui/src/theming/themes")["light"],
          "--radius-box": "1rem",
          "--radius-btn": "0.5rem",
          "--radius-badge": "1.9rem",
        },
        dark: {
          ...require("daisyui/src/theming/themes")["dark"],
          "--radius-box": "1rem",
          "--radius-btn": "0.5rem",
          "--radius-badge": "1.9rem",
        },
        valentine: {
          ...require("daisyui/src/theming/themes")["valentine"],
          "--radius-box": "1rem",
          "--radius-btn": "0.5rem",
          "--radius-badge": "1.9rem",
        }
      }
    ],
  }
}
