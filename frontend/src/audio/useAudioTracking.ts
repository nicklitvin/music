import { useCallback, useRef, useState } from 'react'
import type { NoteBoundingBox, ScorePositionEvent, TrackingEvent } from '../lib/types'

interface UseAudioTrackingOptions {
  // Fired for every frame, for the raw detection log.
  onTracking: (event: TrackingEvent) => void
  // Fired only when the backend reports a score position (i.e. the socket
  // was INIT'd with a score).
  onPosition?: (event: ScorePositionEvent) => void
  // The score's notes. Sent to the backend on connect so it can run
  // position tracking; omit to get plain note detection only.
  scoreNotes?: NoteBoundingBox[]
  wsPath?: string
}

// Mirrors VITE_API_BASE_URL from api.ts: same-origin (proxied by Vite) in
// dev, an explicit backend URL in production.
function resolveWsUrl(wsPath: string): string {
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL
  if (apiBaseUrl) {
    return apiBaseUrl.replace(/^http/, 'ws') + wsPath
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}${wsPath}`
}

export function useAudioTracking({
  onTracking,
  onPosition,
  scoreNotes,
  wsPath = '/ws/track-audio',
}: UseAudioTrackingOptions) {
  const [isTracking, setIsTracking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const audioContextRef = useRef<AudioContext | null>(null)
  const workletNodeRef = useRef<AudioWorkletNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const socketRef = useRef<WebSocket | null>(null)

  const start = useCallback(async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const socket = new WebSocket(resolveWsUrl(wsPath))
      socket.binaryType = 'arraybuffer'
      socketRef.current = socket

      socket.onmessage = (event) => {
        const data: TrackingEvent = JSON.parse(event.data)
        onTracking(data)
        if (data.type === 'POSITION') onPosition?.(data)
      }

      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve()
        socket.onerror = () => reject(new Error('WebSocket connection failed'))
      })

      // Hand the score over before any audio, so the backend can track
      // position rather than just detect notes.
      if (scoreNotes && scoreNotes.length > 0) {
        socket.send(JSON.stringify({ type: 'INIT', notes: scoreNotes }))
      }

      const audioContext = new AudioContext()
      audioContextRef.current = audioContext
      await audioContext.audioWorklet.addModule('/worklets/pcm-worklet.js')

      const source = audioContext.createMediaStreamSource(stream)
      const workletNode = new AudioWorkletNode(audioContext, 'pcm-downsampler-processor')
      workletNodeRef.current = workletNode

      workletNode.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(event.data)
        }
      }

      source.connect(workletNode)
      setIsTracking(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start audio tracking')
      stop()
    }
  }, [onTracking, onPosition, scoreNotes, wsPath])

  const stop = useCallback(() => {
    workletNodeRef.current?.disconnect()
    workletNodeRef.current = null

    audioContextRef.current?.close()
    audioContextRef.current = null

    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null

    socketRef.current?.close()
    socketRef.current = null

    setIsTracking(false)
  }, [])

  // Tell the backend the reader has moved to a given onset by hand. A
  // scroll is a soft nudge; a click on the sheet ("I am exactly here") is
  // firm. Either way it is folded into the tracker's belief.
  const sendHint = useCallback((onsetIndex: number, options: { firm?: boolean } = {}) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'HINT', onsetIndex, firm: options.firm ?? false }))
    }
  }, [])

  // Tell the backend which music is actually on screen. It confines the
  // model to that range, so instead of ranking the whole score it only has
  // to notice the move to the next line. Pass null to lift the limit.
  const sendViewport = useCallback((range: { firstOnset: number; lastOnset: number } | null) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'VIEWPORT', ...(range ?? {}) }))
    }
  }, [])

  return { isTracking, error, start, stop, sendHint, sendViewport }
}
