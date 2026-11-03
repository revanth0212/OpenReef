import service from './index'

/**
 * Get the multi-LLM provider registry state (no secrets included).
 */
export const getLLMProviders = () => {
  return service.get('/api/llm/providers')
}

/**
 * Test connectivity to a provider with a tiny chat completion.
 * @param {Object} data - { base_url, api_key, model }
 */
export const testLLMProvider = (data) => {
  return service.post('/api/llm/providers/test', data)
}
