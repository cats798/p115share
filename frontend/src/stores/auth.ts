import { defineStore } from 'pinia'
import axios from 'axios'

interface User {
  username: string;
  avatar_url?: string;
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: null as User | null,
    loading: false
  }),
  getters: {
    isAuthenticated: (state) => !!state.token
  },
  actions: {
    async login(username: string, password: string) {
      this.loading = true
      try {
        const formData = new FormData()
        formData.append('username', username)
        formData.append('password', password)
        
        const res = await axios.post('/api/auth/login', formData)
        this.token = res.data.access_token
        localStorage.setItem('token', this.token)
        axios.defaults.headers.common['Authorization'] = `Bearer ${this.token}`
        await this.fetchProfile()
        return true
      } catch (error) {
        console.error('Login failed:', error)
        throw error
      } finally {
        this.loading = false
      }
    },
    async fetchProfile() {
      if (!this.token) return
      try {
        const res = await axios.get('/api/auth/profile')
        this.user = res.data
      } catch (error) {
        this.logout()
      }
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      delete axios.defaults.headers.common['Authorization']
    }
  }
})
