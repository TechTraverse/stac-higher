/**
 * CodeMirror 6 editor for process code (ADR 0017 §6, closes I-65).
 *
 * Scope: the process editor ONLY. Every other textarea in the app stays a
 * textarea — the brief is explicit about that, and a code editor earns its
 * bytes only where people actually write code.
 *
 * Built from the individual `@codemirror/*` packages rather than the
 * `codemirror` meta-package, so the dependency surface is exactly what is
 * used: state, view, commands, language + the two grammars. Versions are
 * pinned exactly (see the commit for the supply-chain note).
 *
 * The theme is written here against the app's CSS variables rather than
 * pulling a theme package: one fewer dependency, and it follows the NOAA
 * palette in both light and dark for free.
 *
 * Accessibility: the editor keeps the `aria-label` its textarea predecessor
 * had, so anything targeting "Process code" still finds it.
 */
import { useEffect, useRef } from "react";
import { useStore } from "@nanostores/react";
import { EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder as placeholderExt,
} from "@codemirror/view";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import {
  HighlightStyle,
  bracketMatching,
  foldGutter,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { python } from "@codemirror/lang-python";
import { json } from "@codemirror/lang-json";
import { tags as t } from "@lezer/highlight";
import { $theme } from "@/stores/uiStore";

export type CodeLanguage = "python" | "json";

/**
 * Token colours per theme. Deliberately restrained: keywords, strings,
 * comments and numbers carry the weight; everything else inherits.
 */
function highlightStyle(dark: boolean) {
  const c = dark
    ? {
        keyword: "#8ec4e8",
        string: "#7fd1a8",
        comment: "#7b8fa3",
        number: "#e0a94d",
        def: "#cfe3f5",
        type: "#4faf6f",
        meta: "#93a9c0",
      }
    : {
        keyword: "#005ea2",
        string: "#0f766e",
        comment: "#5c7080",
        number: "#a34a12",
        def: "#0b4778",
        type: "#157f3d",
        meta: "#5c7080",
      };
  return HighlightStyle.define([
    { tag: [t.keyword, t.controlKeyword, t.moduleKeyword], color: c.keyword },
    { tag: [t.string, t.special(t.string)], color: c.string },
    { tag: [t.comment, t.lineComment, t.blockComment], color: c.comment, fontStyle: "italic" },
    { tag: [t.number, t.bool, t.null], color: c.number },
    { tag: [t.definition(t.variableName), t.function(t.variableName)], color: c.def },
    { tag: [t.typeName, t.className, t.propertyName], color: c.type },
    { tag: [t.operator, t.punctuation, t.meta], color: c.meta },
    { tag: t.invalid, color: "#e5645b" },
  ]);
}

function editorTheme(dark: boolean): Extension {
  return EditorView.theme(
    {
      "&": {
        backgroundColor: "var(--color-card)",
        color: "var(--color-foreground)",
        fontSize: "13px",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
      },
      "&.cm-focused": {
        outline: "2px solid var(--color-ring)",
        outlineOffset: "-1px",
      },
      ".cm-scroller": {
        fontFamily: "var(--font-mono)",
        lineHeight: "1.6",
      },
      ".cm-content": { padding: "10px 0" },
      ".cm-gutters": {
        backgroundColor: "transparent",
        color: "var(--color-muted-foreground)",
        border: "none",
        paddingRight: "4px",
      },
      ".cm-activeLine": { backgroundColor: "var(--color-accent)" },
      ".cm-activeLineGutter": {
        backgroundColor: "transparent",
        color: "var(--color-foreground)",
      },
      ".cm-cursor, .cm-dropCursor": {
        borderLeftColor: "var(--color-foreground)",
      },
      "&.cm-editor .cm-selectionBackground, .cm-selectionBackground, ::selection":
        { backgroundColor: "var(--color-accent)" },
      ".cm-matchingBracket, .cm-nonmatchingBracket": {
        backgroundColor: "var(--color-muted)",
        outline: "1px solid var(--color-border)",
      },
      ".cm-placeholder": { color: "var(--color-muted-foreground)" },
    },
    { dark },
  );
}

export function CodeEditor({
  value,
  onChange,
  language = "python",
  disabled = false,
  ariaLabel,
  placeholder,
  minHeight = "20rem",
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  language?: CodeLanguage;
  disabled?: boolean;
  ariaLabel: string;
  placeholder?: string;
  minHeight?: string;
  className?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // Kept in a ref so the change listener never goes stale without tearing the
  // whole editor down and losing the cursor.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const theme = useStore($theme);
  const dark = theme === "dark";

  useEffect(() => {
    if (!host.current) return;

    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        foldGutter(),
        history(),
        indentOnInput(),
        bracketMatching(),
        highlightActiveLine(),
        highlightActiveLineGutter(),
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        language === "json" ? json() : python(),
        syntaxHighlighting(highlightStyle(dark)),
        editorTheme(dark),
        EditorView.lineWrapping,
        EditorView.editable.of(!disabled),
        EditorState.readOnly.of(disabled),
        EditorView.contentAttributes.of({
          "aria-label": ariaLabel,
          spellcheck: "false",
        }),
        ...(placeholder ? [placeholderExt(placeholder)] : []),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChangeRef.current(update.state.doc.toString());
          }
        }),
      ],
    });

    const instance = new EditorView({ state, parent: host.current });
    view.current = instance;
    return () => {
      instance.destroy();
      view.current = null;
    };
    // Rebuilt on theme / language / editability changes: reconfiguring
    // compartments for three rarely-flipped inputs is more machinery than the
    // rebuild costs, and the doc is re-seeded from `value` below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dark, language, disabled, ariaLabel, placeholder]);

  // External updates (a deploy re-syncing to the live revision) are pushed in
  // without clobbering what the user is typing.
  useEffect(() => {
    const instance = view.current;
    if (!instance) return;
    const current = instance.state.doc.toString();
    if (current === value) return;
    instance.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
  }, [value]);

  return (
    <div
      ref={host}
      className={className}
      style={{ minHeight }}
      data-testid="code-editor"
    />
  );
}
