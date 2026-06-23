import type { Config } from 'tailwindcss';

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        graphite: '#111416',
        tealsteel: '#2f6f6d',
      },
      borderRadius: {
        ui: '8px',
      },
    },
  },
  plugins: [],
} satisfies Config;
