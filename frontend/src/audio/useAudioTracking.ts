import { useCallback, useRef, useState } from 'react'
import type { NoteDetectionEvent } from '../lib/types'

interface UseAudioTrackingOptions {
  onNoteDetection: (event: NoteDetectionEvent) => void
  wsPath?: string
}

// Mirrors VITE_API_BASE_URL from api.ts: same-origin (proxied by Vite) in
// dev, an explicit Railway backend URL in production.
function resolveWsUrl(wsPath: string): string {
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL
  if (apiBaseUrl) {
    return apiBaseUrl.replace(/^http/, 'ws') + wsPath
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}${wsPath}`
}

export function useAudioTracking({ onNoteDetection, wsPath = '/ws/track-audio' }: UseAudioTrackingOptions) {
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
        const data: NoteDetectionEvent = JSON.parse(event.data)
        onNoteDetection(data)
      }

      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve()
        socket.onerror = () => reject(new Error('WebSocket connection failed'))
      })

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
  }, [onNoteDetection, wsPath])

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

  return { isTracking, error, start, stop }
}
