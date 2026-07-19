"use client";

import "katex/dist/katex.min.css";

import { Check, Copy } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

const sanitizeSchema = {
    ...defaultSchema,
    tagNames: [...(defaultSchema.tagNames ?? []), "img"],
    attributes: {
        ...defaultSchema.attributes,
        code: [...(defaultSchema.attributes?.code ?? []), ["className"]],
        span: [...(defaultSchema.attributes?.span ?? []), ["className"], ["style"]],
        div: [...(defaultSchema.attributes?.div ?? []), ["className"], ["style"]],
        img: [
            ...(defaultSchema.attributes?.img ?? []),
            ["src"],
            ["alt"],
            ["title"],
            ["width"],
            ["height"],
            ["className"],
        ],
    },
    protocols: {
        ...defaultSchema.protocols,
        src: [...(defaultSchema.protocols?.src ?? ["http", "https"]), "http", "https"],
    },
};

function CodeBlock({ text, lang }: { text: string; lang?: string }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
    };
    return (
        <div className="chat-code-block group/code my-3">
            <div className="flex items-center justify-between border-b border-[var(--chat-border)] px-3 py-1.5">
                <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--chat-text-muted)]">
                    {lang || "code"}
                </span>
                <button
                    type="button"
                    onClick={() => void copy()}
                    className="flex items-center gap-1 text-[11px] text-[var(--chat-text-muted)] opacity-0 transition group-hover/code:opacity-100 hover:text-[var(--chat-text)]"
                    aria-label="Kopiuj kod"
                >
                    {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                    {copied ? "Skopiowano" : "Kopiuj"}
                </button>
            </div>
            <pre className="overflow-x-auto p-3">
                <code className={lang ? `language-${lang}` : undefined}>{text}</code>
            </pre>
        </div>
    );
}

function MermaidBlock({ source }: { source: string }) {
    const reactId = useId().replace(/:/g, "");
    const [svg, setSvg] = useState<string | null>(null);
    const [failed, setFailed] = useState(false);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const mermaid = (await import("mermaid")).default;
                mermaid.initialize({
                    startOnLoad: false,
                    theme: "dark",
                    securityLevel: "strict",
                });
                const id = `chat-mmd-${reactId}-${Math.random().toString(16).slice(2, 7)}`;
                const { svg: rendered } = await mermaid.render(id, source);
                if (!cancelled) setSvg(rendered);
            } catch {
                if (!cancelled) setFailed(true);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [reactId, source]);

    if (failed) return <CodeBlock text={source} lang="mermaid" />;
    if (!svg) return <div className="chat-skeleton my-3 h-28" />;
    return (
        <div
            className="my-3 overflow-x-auto border border-[var(--chat-border)] p-3"
            dangerouslySetInnerHTML={{ __html: svg }}
        />
    );
}

const components: Components = {
    code({ className, children, ...props }) {
        const match = /language-(\w+)/.exec(className || "");
        const lang = match?.[1];
        const text = String(children).replace(/\n$/, "");
        const isBlock = Boolean(lang) || text.includes("\n");
        if (lang === "mermaid" && isBlock) return <MermaidBlock source={text} />;
        if (!isBlock) {
            return (
                <code className={className} {...props}>
                    {children}
                </code>
            );
        }
        return <CodeBlock text={text} lang={lang} />;
    },
    table({ children }) {
        return (
            <div className="chat-table-wrap my-3 overflow-x-auto">
                <table>{children}</table>
            </div>
        );
    },
    a({ children, href, ...props }) {
        return (
            <a href={href} className="chat-md-link" {...props}>
                {children}
            </a>
        );
    },
    img({ src, alt, ...props }) {
        const raw = typeof src === "string" ? src : "";
        // Only allow same-origin chat file URLs (generated / uploaded images).
        const ok =
            raw.startsWith("/api/aihub/chat/file/") ||
            raw.startsWith("/chat/file/");
        if (!ok) return null;
        return (
            // eslint-disable-next-line @next/next/no-img-element
            <img
                src={raw}
                alt={alt || "obraz"}
                className="chat-md-img my-3 max-h-[28rem] max-w-full rounded-md border border-[var(--chat-border)]"
                loading="lazy"
                {...props}
            />
        );
    },
};

export function ChatMarkdown({ content }: { content: string }) {
    return (
        <div className="chat-md">
            <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[
                    [rehypeKatex, { throwOnError: false }],
                    [rehypeSanitize, sanitizeSchema],
                ]}
                components={components}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
}
