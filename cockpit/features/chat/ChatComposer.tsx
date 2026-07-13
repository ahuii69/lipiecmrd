"use client";

import {
    ImageIcon,
    Mic,
    Paperclip,
    RotateCcw,
    Send,
    Square,
    X,
} from "lucide-react";
import Image from "next/image";
import {
    ChangeEvent,
    FormEvent,
    KeyboardEvent,
    useEffect,
    useRef,
    useState,
} from "react";

import { COMPOSER_PLACEHOLDER } from "@/features/chat/chat-constants";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { transcribeChatAudio } from "@/lib/api/chat-stt";
import type { DraftAttachment } from "@/lib/chat/draft-attachments";
import { cn } from "@/lib/utils";

export type ChatDraftAttachment = DraftAttachment;

function fileKindLabel(kind?: "text" | "image"): string {
    return kind === "image" ? "obraz" : "plik";
}

export function ChatComposer({
    onSend,
    onRetry,
    onStop,
    disabled,
    retryDisabled,
    stopVisible = false,
    draftFiles,
    onRemoveDraft,
    onPickFiles,
    attachDisabled = false,
    voiceApiKeyOverride,
    suggestion,
    onSuggestionConsumed,
}: {
    onSend: (text: string, opts?: { sttUsed?: boolean }) => Promise<void>;
    onRetry: () => Promise<void>;
    onStop?: () => void;
    disabled: boolean;
    retryDisabled: boolean;
    stopVisible?: boolean;
    draftFiles: ChatDraftAttachment[];
    onRemoveDraft: (key: string) => void;
    onPickFiles: (files: FileList | null) => void;
    attachDisabled?: boolean;
    voiceApiKeyOverride?: string;
    suggestion?: string | null;
    onSuggestionConsumed?: () => void;
}) {
    const [value, setValue] = useState("");
    const fileRef = useRef<HTMLInputElement | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const chunksRef = useRef<BlobPart[]>([]);
    const [voicePhase, setVoicePhase] = useState<
        "idle" | "recording" | "processing" | "error"
    >("idle");
    const [voiceError, setVoiceError] = useState<string | null>(null);
    const lastInputViaSttRef = useRef(false);

    useEffect(() => {
        if (!suggestion) return;
        setValue(suggestion);
        lastInputViaSttRef.current = false;
        onSuggestionConsumed?.();
        requestAnimationFrame(() => textareaRef.current?.focus());
    }, [suggestion, onSuggestionConsumed]);

    useEffect(
        () => () => {
            const recorder = mediaRecorderRef.current;
            if (recorder && recorder.state === "recording") recorder.stop();
            mediaRecorderRef.current = null;
        },
        [],
    );

    const submitMessage = async (text: string) => {
        const previous = value;
        try {
            await onSend(text, { sttUsed: lastInputViaSttRef.current });
            lastInputViaSttRef.current = false;
            setValue("");
        } catch {
            setValue(previous);
        }
    };

    const submit = async (e: FormEvent) => {
        e.preventDefault();
        const text = value.trim();
        if (!text || disabled) return;
        await submitMessage(text);
    };

    const startRecording = async () => {
        setVoiceError(null);
        if (voicePhase === "recording") {
            mediaRecorderRef.current?.stop();
            return;
        }
        if (!navigator.mediaDevices?.getUserMedia) {
            setVoicePhase("error");
            setVoiceError("Ta przeglądarka nie wspiera nagrywania audio.");
            return;
        }
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            chunksRef.current = [];
            const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
                ? "audio/webm;codecs=opus"
                : MediaRecorder.isTypeSupported("audio/webm")
                  ? "audio/webm"
                  : "audio/mp4";
            const recorder = new MediaRecorder(stream, { mimeType: mime });
            mediaRecorderRef.current = recorder;
            recorder.ondataavailable = (ev) => {
                if (ev.data.size > 0) chunksRef.current.push(ev.data);
            };
            recorder.onstop = () => {
                stream.getTracks().forEach((track) => track.stop());
                mediaRecorderRef.current = null;
                const blob = new Blob(chunksRef.current, { type: mime });
                chunksRef.current = [];
                if (blob.size < 16) {
                    setVoicePhase("idle");
                    return;
                }
                void (async () => {
                    setVoicePhase("processing");
                    const ext = mime.includes("webm") ? "webm" : "mp4";
                    const res = await transcribeChatAudio(
                        blob,
                        `dictation.${ext}`,
                        voiceApiKeyOverride,
                    );
                    if (!res.ok || !res.text) {
                        setVoicePhase("error");
                        setVoiceError(res.error || "Transkrypcja nieudana.");
                        return;
                    }
                    setValue((current) => {
                        const transcript = res.text!.trim();
                        lastInputViaSttRef.current = true;
                        return current.trim()
                            ? `${current.trim()} ${transcript}`
                            : transcript;
                    });
                    setVoicePhase("idle");
                })();
            };
            recorder.start(200);
            setVoicePhase("recording");
        } catch (err: unknown) {
            setVoicePhase("error");
            const secure =
                typeof window !== "undefined" && window.isSecureContext;
            if (!secure) {
                setVoiceError("Mikrofon wymaga HTTPS albo localhost.");
                return;
            }
            const name =
                err instanceof DOMException ? err.name : (err as Error)?.name;
            if (name === "NotAllowedError" || name === "PermissionDeniedError") {
                setVoiceError("Brak zgody na mikrofon.");
                return;
            }
            setVoiceError("Nie udało się otworzyć mikrofonu.");
        }
    };

    const canSend =
        Boolean(value.trim()) &&
        !disabled &&
        !draftFiles.some((d) => d.status === "uploading");

    return (
        <div className="chat-composer-wrap">
            <form
                onSubmit={submit}
                className="chat-composer-inner mx-auto w-full max-w-[860px]"
                data-testid="user-chat-composer"
            >
                <input
                    ref={fileRef}
                    type="file"
                    multiple
                    className="hidden"
                    data-testid="user-chat-file-input"
                    accept=".txt,.md,.pdf,.png,.jpg,.jpeg,.webp,text/plain,text/markdown,application/pdf,image/png,image/jpeg,image/webp"
                    onChange={(e: ChangeEvent<HTMLInputElement>) => {
                        onPickFiles(e.target.files);
                        e.target.value = "";
                    }}
                />

                {draftFiles.length > 0 ? (
                    <div className="mb-2 flex flex-wrap gap-2">
                        {draftFiles.map((file) => (
                            <div
                                key={file.key}
                                className="flex max-w-full items-center gap-2 border border-[var(--chat-border)] bg-[#15181D] px-2 py-1.5 text-sm"
                            >
                                {file.previewUrl ? (
                                    <Image
                                        src={file.previewUrl}
                                        alt=""
                                        width={32}
                                        height={32}
                                        unoptimized
                                        className="h-8 w-8 object-cover"
                                    />
                                ) : (
                                    <ImageIcon className="h-4 w-4 text-[var(--chat-text-muted)]" />
                                )}
                                <span className="truncate text-[var(--chat-text)]">
                                    {file.filename}
                                </span>
                                <button
                                    type="button"
                                    onClick={() => onRemoveDraft(file.key)}
                                    aria-label={`Usuń ${file.filename}`}
                                >
                                    <X className="h-4 w-4 text-[var(--chat-text-muted)]" />
                                </button>
                            </div>
                        ))}
                    </div>
                ) : null}

                {voiceError ? (
                    <p className="mb-2 text-sm text-amber-200/90">{voiceError}</p>
                ) : null}

                <div className="chat-composer-field flex min-w-0 items-end gap-1">
                    <button
                        type="button"
                        className="chat-composer-icon-btn"
                        disabled={disabled || attachDisabled}
                        aria-label="Dodaj plik"
                        data-testid="user-chat-attach"
                        onClick={() => fileRef.current?.click()}
                    >
                        <Paperclip className="h-5 w-5" />
                    </button>
                    <button
                        type="button"
                        className={cn(
                            "chat-composer-icon-btn",
                            voicePhase === "recording" && "text-red-400",
                        )}
                        disabled={disabled || voicePhase === "processing"}
                        aria-label="Dyktuj"
                        data-testid="user-chat-mic"
                        onClick={() => void startRecording()}
                    >
                        <Mic className="h-5 w-5" />
                    </button>
                    <Textarea
                        ref={textareaRef}
                        data-testid="user-chat-input"
                        value={value}
                        onChange={(e) => {
                            lastInputViaSttRef.current = false;
                            setValue(e.target.value);
                        }}
                        placeholder={COMPOSER_PLACEHOLDER}
                        onKeyDown={async (e: KeyboardEvent<HTMLTextAreaElement>) => {
                            if (
                                e.key === "Enter" &&
                                !e.shiftKey &&
                                !e.ctrlKey &&
                                !e.metaKey &&
                                !e.altKey
                            ) {
                                e.preventDefault();
                                const text = value.trim();
                                if (!text || disabled) return;
                                await submitMessage(text);
                            }
                        }}
                        rows={1}
                        disabled={disabled}
                        className="composer-textarea min-h-[56px] max-h-[180px] flex-1 resize-none border-0 bg-transparent px-2 py-3.5 text-base text-[var(--chat-text)] shadow-none placeholder:text-[var(--chat-text-muted)] focus-visible:ring-0 max-md:min-h-[58px]"
                    />
                    {stopVisible && onStop ? (
                        <button
                            type="button"
                            className="chat-composer-send chat-composer-send--stop"
                            data-testid="user-chat-stop"
                            aria-label="Zatrzymaj"
                            onClick={onStop}
                        >
                            <Square className="h-4 w-4" />
                        </button>
                    ) : (
                        <button
                            type="submit"
                            className={cn(
                                "chat-composer-send",
                                canSend && "chat-composer-send--active",
                            )}
                            data-testid="user-chat-send"
                            aria-label="Wyślij"
                            disabled={!canSend}
                        >
                            <Send className="h-4 w-4" />
                        </button>
                    )}
                </div>

                <div className="mt-2 flex items-center justify-between gap-2 px-1">
                    <p className="hidden text-[11px] text-[var(--chat-text-muted)] sm:block">
                        Enter — wyślij · Shift+Enter — nowa linia
                    </p>
                    <button
                        type="button"
                        data-testid="user-chat-retry"
                        className="ml-auto flex items-center gap-1 text-xs text-[var(--chat-text-muted)] hover:text-[var(--chat-text)] disabled:opacity-40"
                        onClick={onRetry}
                        disabled={disabled || retryDisabled}
                    >
                        <RotateCcw className="h-3 w-3" />
                        Ponów
                    </button>
                </div>
            </form>
        </div>
    );
}
