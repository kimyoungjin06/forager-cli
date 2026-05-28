/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'Inter Fallback', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        brand: {
          50: '#eef9ff',
          100: '#d9f1ff',
          200: '#b8e5ff',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0075ba',
          600: '#00639d',
          700: '#00507f',
          800: '#063f63',
          900: '#0a324f',
        },
        kisti: {
          blue: '#0075ba',
          red: '#da2128',
          cyan: '#38bdf8',
        },
        surface: {
          50: '#f8fafc',
          100: '#f1f5f9',
          200: '#e2e8f0',
          700: '#334155',
          800: '#1e293b',
          850: '#162032',
          900: '#0f172a',
          950: '#020617',
        },
      },
    },
  },
  plugins: [],
};
