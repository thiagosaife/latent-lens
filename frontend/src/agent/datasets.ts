import { authHeaders } from './http'

export interface DatasetColumn {
  name: string
  type: 'numeric' | 'categorical'
  missing: number
}

export interface DatasetMeta {
  datasetId: string
  name: string
  rows: number
  numeric: number
  categorical: number
  duplicates: number
  columns: DatasetColumn[]
}

/**
 * Upload a CSV/Parquet file; the backend parses it, stores it, and returns a
 * datasetId + a quick profile. Pass the datasetId when starting a run to analyze
 * the uploaded data instead of the synthetic default.
 */
export async function uploadDataset(file: File): Promise<DatasetMeta> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/datasets', { method: 'POST', body: form, headers: authHeaders() })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // non-JSON error body
    }
    throw new Error(detail)
  }
  return (await res.json()) as DatasetMeta
}
