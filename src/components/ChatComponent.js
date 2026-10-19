import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Tooltip } from "antd";
import { AudioOutlined, SendOutlined } from "@ant-design/icons";
import SpeechRecognition, {
  useSpeechRecognition,
} from "react-speech-recognition";

const { Search } = Input;

/**
 * Text input + voice mode.
 * onAsk(question) resolves to the answer object ({ status, text, ... }).
 */
const ChatComponent = ({ onAsk, isLoading, mode, hasDocument, speech }) => {
  const [value, setValue] = useState("");
  const [voiceMode, setVoiceMode] = useState(false);
  const voiceModeRef = useRef(voiceMode);
  voiceModeRef.current = voiceMode;

  const {
    transcript,
    listening,
    resetTranscript,
    browserSupportsSpeechRecognition,
    isMicrophoneAvailable,
  } = useSpeechRecognition();

  const startListening = () => {
    resetTranscript();
    SpeechRecognition.startListening();
  };

  const submit = async (raw) => {
    const question = (raw ?? "").trim();
    if (!question || isLoading) return;
    setValue("");
    const answer = await onAsk(question);

    // Keep the question in the box if it failed, so it can be retried.
    if (answer?.status === "error") {
      setValue(question);
      return;
    }

    if (voiceModeRef.current && speech?.ready) {
      await speech.speak(answer?.text);
      if (voiceModeRef.current) startListening(); // continue the conversation
    }
  };

  // When the user stops talking, send what they said.
  useEffect(() => {
    if (voiceMode && !listening && transcript) {
      const said = transcript;
      resetTranscript();
      submit(said);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listening]);

  const toggleVoiceMode = () => {
    const next = !voiceMode;
    setVoiceMode(next);
    SpeechRecognition.stopListening();
    speech?.stop();
    resetTranscript();
  };

  const toggleRecording = () => {
    if (listening) {
      SpeechRecognition.stopListening();
    } else {
      speech?.stop();
      startListening();
    }
  };

  const voiceUnsupported = !browserSupportsSpeechRecognition;

  return (
    <div className="chat-bar">
      {voiceMode && !isMicrophoneAvailable && (
        <Alert
          type="warning"
          showIcon
          message="Microphone access is blocked. Allow it in your browser settings to use voice mode."
          className="chat-alert"
        />
      )}

      {voiceMode && (
        <div className="voice-panel" aria-live="polite">
          <span className={`mic-dot ${listening ? "is-listening" : ""}`} />
          <span className="voice-transcript">
            {listening
              ? transcript || "Listening… start speaking"
              : isLoading
              ? "Thinking…"
              : speech?.speaking
              ? "Speaking…"
              : "Tap the mic and ask a question"}
          </span>
        </div>
      )}

      <div className="chat-row">
        {!voiceMode ? (
          <Search
            placeholder={
              mode === "agent"
                ? "Give the agent a task, e.g. “Check this site for broken links”…"
                : hasDocument
                ? "Ask about your document…"
                : "Ask anything, or upload a PDF to ask about it…"
            }
            enterButton={
              <Button
                type="primary"
                icon={<SendOutlined />}
                aria-label={mode === "agent" ? "Run" : "Ask"}
              >
                {mode === "agent" ? "Run" : "Ask"}
              </Button>
            }
            size="large"
            onSearch={submit}
            loading={isLoading}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label="Question"
            allowClear
          />
        ) : (
          <Button
            type="primary"
            size="large"
            shape="round"
            icon={<AudioOutlined />}
            danger={listening}
            disabled={isLoading}
            onClick={toggleRecording}
            className={`record-btn ${listening ? "is-listening" : ""}`}
            aria-label={listening ? "Stop recording" : "Start recording"}
          >
            {listening ? "Listening… tap to stop" : "Tap to speak"}
          </Button>
        )}

        <Tooltip
          title={
            voiceUnsupported
              ? "Voice isn't supported in this browser (try Chrome or Edge)."
              : voiceMode
              ? "Switch back to typing"
              : "Talk to AskScout and hear answers read aloud"
          }
        >
          <Button
            size="large"
            shape="round"
            type={voiceMode ? "primary" : "default"}
            ghost={voiceMode}
            icon={<AudioOutlined />}
            onClick={toggleVoiceMode}
            disabled={voiceUnsupported}
            aria-pressed={voiceMode}
            aria-label="Toggle voice mode"
          >
            Voice mode{voiceMode ? ": on" : ""}
          </Button>
        </Tooltip>
      </div>
    </div>
  );
};

export default ChatComponent;
