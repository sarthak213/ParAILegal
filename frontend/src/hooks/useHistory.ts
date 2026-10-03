import { useState, useCallback } from 'react'
import type { HistoryEntry, SourceChunk, QueryMode } from '../types'

const STORAGE_KEY = 'parailegal_history'
const MAX_ENTRIES = 50

function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch {
    // localStorage full — remove oldest entry and retry
    const trimmed = entries.slice(1)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed))
  }
}

export function useHistory() {
  const [entries, setEntries] = useState<HistoryEntry[]>(loadHistory)

  const addEntry = useCallback((
    query: string,
    rawQuery: string,
    answer: string,
    sources: SourceChunk[],
    domain: string,
    mode: QueryMode,
  ): string => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    const entry: HistoryEntry = {
      id,
      query,
      rawQuery,
      answer,
      sources,
      domain,
      mode,
      timestamp: Date.now(),
    }
    setEntries(prev => {
      const updated = [entry, ...prev].slice(0, MAX_ENTRIES)
      saveHistory(updated)
      return updated
    })
    return id
  }, [])

  const removeEntry = useCallback((id: string) => {
    setEntries(prev => {
      const updated = prev.filter(e => e.id !== id)
      saveHistory(updated)
      return updated
    })
  }, [])

  const clearAll = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setEntries([])
  }, [])

  return { entries, addEntry, removeEntry, clearAll }
}
