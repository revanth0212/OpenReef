import { createI18n } from 'vue-i18n'
import languages from '../../../locales/languages.json'

const localeFiles = import.meta.glob('../../../locales/!(languages).json', { eager: true })

const messages = {}
const availableLocales = []

for (const path in localeFiles) {
  const key = path.match(/\/([^/]+)\.json$/)[1]
  if (languages[key]) {
    messages[key] = localeFiles[path].default
    availableLocales.push({ key, label: languages[key].label })
  }
}

// Saved choice wins; first-time visitors start from their browser language.
const detectBrowserLocale = () => {
  const nav = (navigator.language || '').toLowerCase()
  const primary = nav.split('-')[0]
  return languages[primary] ? primary : 'zh'
}

const savedLocale = localStorage.getItem('locale') || detectBrowserLocale()

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: 'zh',
  messages
})

export { availableLocales }
export default i18n
