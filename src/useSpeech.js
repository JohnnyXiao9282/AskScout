import { useCallback, useEffect, useRef, useState } from "react";
import Speech from "speak-tts";

/** Shared text-to-speech helper built on speak-tts. */
const useSpeech = () => {
  const speechRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  useEffect(() => {
    const speech = new Speech();
    if (!speech.hasBrowserSupport?.()) return;
    speech
      .init({
        volume: 1,
        lang: "en-US",
        rate: 1,
        pitch: 1,
        voice: "Google US English",
        splitSentences: false,
      })
      .then(() => {
        speechRef.current = speech;
        setReady(true);
      })
      .catch((e) => console.error("Speech init failed:", e));
  }, []);

  const speak = useCallback((text) => {
    const speech = speechRef.current;
    if (!speech || !text) return Promise.resolve();
    setSpeaking(true);
    // Strip markdown symbols so they aren't read aloud.
    const plain = String(text).replace(/[*_#`>[\]]/g, "");
    return speech
      .speak({ text: plain, queue: false })
      .catch((e) => console.error("Speech failed:", e))
      .finally(() => setSpeaking(false));
  }, []);

  const stop = useCallback(() => {
    speechRef.current?.cancel();
    setSpeaking(false);
  }, []);

  return { ready, speaking, speak, stop };
};

export default useSpeech;
