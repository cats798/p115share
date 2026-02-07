import { defineStore } from 'pinia'

export type ThemeMode = 'light' | 'dark'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    mode: (localStorage.getItem('theme-mode') as ThemeMode) || 'light'
  }),
  actions: {
    toggleTheme() {
      this.mode = this.mode === 'light' ? 'dark' : 'light'
      localStorage.setItem('theme-mode', this.mode)
      if (this.mode === 'dark') {
        document.documentElement.classList.add('dark')
      } else {
        document.documentElement.classList.remove('dark')
      }
    },
    initTheme() {
      if (this.mode === 'dark') {
        document.documentElement.classList.add('dark')
      } else {
        document.documentElement.classList.remove('dark')
      }
    }
  }
})
