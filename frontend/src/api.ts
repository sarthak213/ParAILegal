// Where the backend is. In the desktop app the shell starts it on a free port and sets
// window.__PARAILEGAL_API__ before the page loads (src-tauri/src/main.rs); in development it is
// VITE_API_BASE_URL, or the default port.
declare global {
  interface Window { __PARAILEGAL_API__?: string }
}

export const API_BASE: string =
  window.__PARAILEGAL_API__ || import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
