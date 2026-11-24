import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '../services/api'

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        // Never retry client errors (401/403/404/422); retry transient failures twice.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
    },
  })
}
