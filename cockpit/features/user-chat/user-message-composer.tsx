"use client";

import { ImageIcon, Mic, Paperclip, RotateCcw, Send, Square, X } from "lucide-react";
import Image from "next/image";
import { ChangeEvent, FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { transcribeChatAudio } from "@/lib/api/chat-stt";
import { cn } from "@/lib/utils";

export interface UserDraftAttachment { key: string; fileId?: string; filename: string; status: "uploading" | "ready" | "error"; error?: string; kind?: "text" | "image"; previewUrl?: string; }
const TEXTAREA_MIN_PX = 52;
const TEXTAREA_MAX_PX = 260;
function fileKindLabel(kind?: "text" | "image"): string { return kind === "image" ? "obraz" : "plik"; }

export function UserMessageComposer({ onSend, onRetry, onStop, disabled, retryDisabled, stopVisible = false, draftFiles, onRemoveDraft, onPickFiles, attachDisabled = false, voiceApiKeyOverride, suggestion, onSuggestionConsumed }: { onSend: (text: string, opts?: { sttUsed?: boolean }) => Promise<void>; onRetry: () => Promise<void>; onStop?: () => void; disabled: boolean; retryDisabled: boolean; stopVisible?: boolean; draftFiles: UserDraftAttachment[]; onRemoveDraft: (key: string) => void; onPickFiles: (files: FileList | null) => void; attachDisabled?: boolean; voiceApiKeyOverride?: string; suggestion?: string | null; onSuggestionConsumed?: () => void; }) {
    const [value, setValue] = useState("");
    const fileRef = useRef<HTMLInputElement | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const chunksRef = useRef<BlobPart[]>([]);
    const [voicePhase, setVoicePhase] = useState<"idle" | "recording" | "processing" | "error">("idle");
    const [voiceError, setVoiceError] = useState<string | null>(null);
    const lastInputViaSttRef = useRef(false);
    const resizeTextarea = useCallback(() => { const el = textareaRef.current; if (!el) return; el.style.height = "auto"; el.style.height = `${Math.min(Math.max(el.scrollHeight, TEXTAREA_MIN_PX), TEXTAREA_MAX_PX)}px`; }, []);
    useEffect(() => { resizeTextarea(); }, [value, resizeTextarea]);
    useEffect(() => { if (!suggestion) return; setValue(suggestion); lastInputViaSttRef.current = false; onSuggestionConsumed?.(); requestAnimationFrame(() => { textareaRef.current?.focus(); resizeTextarea(); }); }, [suggestion, onSuggestionConsumed, resizeTextarea]);
    useEffect(() => () => { const recorder = mediaRecorderRef.current; if (recorder && recorder.state === "recording") recorder.stop(); mediaRecorderRef.current = null; }, []);
    const submitMessage = async (text: string) => { const previous = value; try { await onSend(text, { sttUsed: lastInputViaSttRef.current }); lastInputViaSttRef.current = false; setValue(""); requestAnimationFrame(resizeTextarea); } catch { setValue(previous); requestAnimationFrame(resizeTextarea); } };
    const submit = async (e: FormEvent) => { e.preventDefault(); const text = value.trim(); if (!text || disabled) return; await submitMessage(text); };
    const startRecording = async () => {
        setVoiceError(null);
        if (voicePhase === "recording") { mediaRecorderRef.current?.stop(); return; }
        if (!navigator.mediaDevices?.getUserMedia) { setVoicePhase("error"); setVoiceError("Ta przeglądarka nie wspiera nagrywania audio."); return; }
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            chunksRef.current = [];
            const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/mp4";
            const recorder = new MediaRecorder(stream, { mimeType: mime });
            mediaRecorderRef.current = recorder;
            recorder.ondataavailable = (ev) => { if (ev.data.size > 0) chunksRef.current.push(ev.data); };
            recorder.onstop = () => { stream.getTracks().forEach((track) => track.stop()); mediaRecorderRef.current = null; const blob = new Blob(chunksRef.current, { type: mime }); chunksRef.current = []; if (blob.size < 16) { setVoicePhase("idle"); return; } void (async () => { setVoicePhase("processing"); const ext = mime.includes("webm") ? "webm" : "mp4"; const res = await transcribeChatAudio(blob, `dictation.${ext}`, voiceApiKeyOverride); if (!res.ok || !res.text) { setVoicePhase("error"); setVoiceError(res.error || "Transkrypcja nieudana."); return; } setValue((current) => { const transcript = res.text!.trim(); lastInputViaSttRef.current = true; return current.trim() ? `${current.trim()} ${transcript}` : transcript; }); setVoicePhase("idle"); })(); };
            recorder.start(200); setVoicePhase("recording");
        } catch (err: unknown) {
            setVoicePhase("error"); const secure = typeof window !== "undefined" && window.isSecureContext; if (!secure) { setVoiceError("Mikrofon wymaga HTTPS albo localhost."); return; } const name = err instanceof DOMException ? err.name : (err as Error)?.name; if (name === "NotAllowedError" || name === "PermissionDeniedError") { setVoiceError("Brak zgody na mikrofon. Sprawdź ustawienia strony."); return; } setVoiceError("Nie udało się otworzyć mikrofonu.");
        }
    };
    const canSend = Boolean(value.trim()) && !disabled && !draftFiles.some((d) => d.status === "uploading");
    return (
        <form onSubmit={submit} className="w-full space-y-3 overflow-x-hidden">
            <input ref={fileRef} type="file" multiple className="hidden" data-testid="user-chat-file-input" accept=".txt,.md,.pdf,.png,.jpg,.jpeg,.webp,text/plain,text/markdown,application/pdf,image/png,image/jpeg,image/webp" onChange={(e: ChangeEvent<HTMLInputElement>) => { onPickFiles(e.target.files); e.target.value = ""; }} />
            {draftFiles.length > 0 ? <div className="flex flex-wrap gap-2">{draftFiles.map((file) => <div key={file.key} className={cn("group/file flex max-w-full items-center gap-2 rounded-2xl border bg-neutral-900/95 px-2.5 py-2 text-sm shadow-lg shadow-black/20", file.status === "error" ? "border-red-400/30" : "border-white/10")}>{file.previewUrl ? <Image src={file.previewUrl} alt="" width={36} height={36} unoptimized className="h-9 w-9 shrink-0 rounded-xl object-cover" /> : <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/6 text-neutral-400"><ImageIcon className="h-4 w-4" /></div>}<div className="min-w-0"><p className="truncate font-medium text-neutral-200" title={file.filename}>{file.filename}</p><p className={cn("text-xs", file.status === "error" ? "text-red-300" : "text-neutral-500")}>{file.status === "uploading" ? "wysyłam…" : file.status === "error" ? file.error || "błąd uploadu" : fileKindLabel(file.kind)}</p></div><button type="button" className="rounded-xl p-1.5 text-neutral-500 transition hover:bg-white/10 hover:text-neutral-100" onClick={() => onRemoveDraft(file.key)} aria-label={`Usuń ${file.filename}`}><X className="h-4 w-4" /></button></div>)}</div> : null}
            {voiceError ? <div className="rounded-2xl border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">{voiceError}</div> : null}
            <div className="rounded-[1.75rem] border border-white/12 bg-neutral-900/95 p-2 shadow-2xl shadow-black/35 ring-1 ring-black/30 backdrop-blur-2xl sm:p-2.5"><div className="flex min-w-0 items-end gap-2">
                <Button type="button" variant="ghost" size="icon" className="h-11 w-11 shrink-0 rounded-2xl text-neutral-300 hover:bg-white/10 hover:text-white" disabled={disabled || attachDisabled} aria-label="Dodaj plik" data-testid="user-chat-attach" onClick={() => fileRef.current?.click()}><Paperclip className="h-5 w-5" /></Button>
                <Button type="button" variant={voicePhase === "recording" ? "destructive" : "ghost"} size="icon" className={cn("h-11 w-11 shrink-0 rounded-2xl text-neutral-300 hover:bg-white/10 hover:text-white", voicePhase === "recording" && "animate-pulse bg-red-500 text-white hover:bg-red-500")} disabled={disabled || voicePhase === "processing"} aria-label={voicePhase === "recording" ? "Zatrzymaj nagrywanie" : "Dyktuj wiadomość"} data-testid="user-chat-mic" onClick={() => void startRecording()}><Mic className="h-5 w-5" /></Button>
                <div className="relative min-w-0 flex-1">{!value ? <div className="pointer-events-none absolute left-3 top-3.5 z-0 line-clamp-1 text-[15px] text-neutral-500 sm:text-base">Napisz wiadomość do AI-Hub…</div> : null}<Textarea ref={textareaRef} data-testid="user-chat-input" value={value} onChange={(e) => { lastInputViaSttRef.current = false; setValue(e.target.value); }} onKeyDown={async (e: KeyboardEvent<HTMLTextAreaElement>) => { const sendByEnter = e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey; if (!sendByEnter) return; e.preventDefault(); const text = value.trim(); if (!text || disabled) return; await submitMessage(text); }} rows={1} style={{ minHeight: TEXTAREA_MIN_PX, maxHeight: TEXTAREA_MAX_PX }} className="relative z-10 min-h-[52px] min-w-0 flex-1 resize-none rounded-2xl border-0 bg-transparent px-3 py-3 text-[16px] leading-7 text-neutral-100 shadow-none focus-visible:outline-none focus-visible:ring-0" disabled={disabled} /></div>
                {stopVisible && onStop ? <Button type="button" variant="destructive" size="sm" data-testid="user-chat-stop" aria-label="Zatrzymaj generowanie" className="h-11 shrink-0 rounded-2xl px-3 font-semibold sm:px-4" onClick={onStop}><Square className="h-4 w-4 sm:mr-2" aria-hidden /><span className="hidden sm:inline">Stop</span></Button> : <Button type="submit" size="sm" data-testid="user-chat-send" aria-label="Wyślij" className="h-11 shrink-0 rounded-2xl bg-white px-3 font-bold text-neutral-950 shadow-lg shadow-white/5 hover:bg-emerald-100 sm:px-5" disabled={!canSend}><Send className="h-4 w-4 sm:mr-2" aria-hidden /><span className="hidden sm:inline">Wyślij</span></Button>}
            </div></div>
            <div className="flex flex-col gap-2 px-1 sm:flex-row sm:items-center sm:justify-between"><p className="text-[11px] leading-snug text-neutral-600 sm:text-xs">Enter wysyła · Shift+Enter robi nową linię · upload do 5 plików.</p><Button type="button" variant="ghost" size="sm" data-testid="user-chat-retry" className="h-9 shrink-0 self-start rounded-xl text-xs font-semibold text-neutral-400 hover:bg-white/5 hover:text-neutral-200 sm:self-center" onClick={onRetry} disabled={disabled || retryDisabled}><RotateCcw className="mr-2 h-4 w-4" />Ponów ostatnią</Button></div>
        </form>
    );
}
