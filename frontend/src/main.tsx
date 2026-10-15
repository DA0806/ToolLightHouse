import '@fontsource/albert-sans/400.css'
import '@fontsource/albert-sans/500.css'
import '@fontsource/albert-sans/700.css'
import '@fontsource/albert-sans/800.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
