// AM6 — dictation is one piece that every composer uses. The chat's composer
// held it inline (two recorders, the counter, the transcript landing in the
// box, errors, the Ctrl+Shift+V toggle, the microphone and the recording
// bar); an agent's workspace needs the same thing, not a copy of it.
import { useRef, useState } from "react";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { voice, whisper } = vi.hoisted(() => ({
  voice: { isSupported: true, start: vi.fn(), stop: vi.fn() },
  whisper: { isSupported: true, isTranscribing: false, start: vi.fn(async () => {}), stop: vi.fn(async () => {}) },
}));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => voice }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => whisper }));

import { ComposerFrame } from "@/components/chat/ComposerFrame";
import { useToastStore } from "@/components/ui/Toast";
import { VOICE_TOGGLE_EVENT } from "@/lib/composerShortcuts";
import { useI18nStore } from "@/lib/i18n";
import { useVoiceStore } from "@/stores/voiceStore";

import { DictationButton, useDictation } from "./Dictation";

const voiceInitial = useVoiceStore.getState();

function AnyComposer({ streaming = false }: { streaming?: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const dictation = useDictation({ setText, textareaRef: ref, streaming });
  return (
    <ComposerFrame
      textareaRef={ref}
      value={text}
      onChange={setText}
      placeholder="พิมพ์"
      canSend={text.length > 0}
      onSubmit={() => {}}
      above={dictation.bar}
      pillHidden={dictation.active}
      rightTools={dictation.supported && !streaming ? <DictationButton onStart={dictation.start} /> : null}
    />
  );
}

beforeEach(() => {
  for (const f of [voice.start, voice.stop, whisper.start, whisper.stop]) f.mockClear();
  voice.isSupported = true;
  whisper.isSupported = true;
  whisper.isTranscribing = false;
  useI18nStore.setState({ locale: "th" });
  useVoiceStore.setState({ ...voiceInitial, inputMethod: "web-speech", isRecording: false, transcript: "", interimTranscript: "", error: null }, true);
  useToastStore.getState().clear();
});
afterEach(cleanup);

const box = () => screen.getByPlaceholderText("พิมพ์") as HTMLTextAreaElement;
const mic = () => screen.queryByRole("button", { name: "ป้อนเสียง" });

describe("AM6 — dictation, for any composer", () => {
  it("the microphone starts the recorder the person chose in voice settings", () => {
    render(<AnyComposer />);
    fireEvent.click(mic()!);
    expect(voice.start).toHaveBeenCalledTimes(1);
    expect(whisper.start).not.toHaveBeenCalled();
    cleanup();
    useVoiceStore.setState({ inputMethod: "whisper" });
    render(<AnyComposer />);
    fireEvent.click(mic()!);
    expect(whisper.start).toHaveBeenCalledTimes(1);
    expect(voice.start).toHaveBeenCalledTimes(1);
  });

  it("falls back to the other recorder where the browser has no speech recognition", () => {
    voice.isSupported = false;
    render(<AnyComposer />);
    fireEvent.click(mic()!);
    expect(whisper.start).toHaveBeenCalledTimes(1);
  });

  it("no recorder at all: no microphone", () => {
    voice.isSupported = false;
    whisper.isSupported = false;
    render(<AnyComposer />);
    expect(mic()).toBeNull();
  });

  it("recording: the bar in place of the pill, with a stop that stops", () => {
    render(<AnyComposer />);
    act(() => useVoiceStore.setState({ isRecording: true, interimTranscript: "ทำไมค่าที่ปรึกษา" }));
    const bar = screen.getByRole("status");
    expect(bar.textContent).toContain("ทำไมค่าที่ปรึกษา");
    expect((box().parentElement!.parentElement as HTMLElement).style.display).toBe("none");
    fireEvent.click(screen.getByRole("button", { name: "หยุดอัดเสียง" }));
    expect(voice.stop).toHaveBeenCalledTimes(1);
  });

  it("what was said is added to what was typed, and the transcript is used once", () => {
    render(<AnyComposer />);
    fireEvent.change(box(), { target: { value: "ช่วยดู" } });
    act(() => useVoiceStore.setState({ isRecording: true }));
    act(() => useVoiceStore.setState({ isRecording: false, transcript: " ทำไมค่าที่ปรึกษาสูงขึ้น " }));
    expect(box().value).toBe("ช่วยดู ทำไมค่าที่ปรึกษาสูงขึ้น");
    expect(useVoiceStore.getState().transcript).toBe("");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("a recorder's error is shown once", () => {
    render(<AnyComposer />);
    act(() => useVoiceStore.setState({ error: "ไมโครโฟนถูกปิดกั้น" }));
    expect(useToastStore.getState().message).toBe("ไมโครโฟนถูกปิดกั้น");
    expect(useVoiceStore.getState().error).toBeNull();
  });

  it("Ctrl+Shift+V toggles it, but does not start over an answer being written", () => {
    const view = render(<AnyComposer />);
    act(() => void window.dispatchEvent(new CustomEvent(VOICE_TOGGLE_EVENT)));
    expect(voice.start).toHaveBeenCalledTimes(1);
    act(() => useVoiceStore.setState({ isRecording: true }));
    act(() => void window.dispatchEvent(new CustomEvent(VOICE_TOGGLE_EVENT)));
    expect(voice.stop).toHaveBeenCalledTimes(1);
    act(() => useVoiceStore.setState({ isRecording: false }));
    view.rerender(<AnyComposer streaming />);
    act(() => void window.dispatchEvent(new CustomEvent(VOICE_TOGGLE_EVENT)));
    expect(voice.start).toHaveBeenCalledTimes(1);
  });
});
