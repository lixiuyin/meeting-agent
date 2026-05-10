import { motion } from "framer-motion";
import { RobotOutlined } from "@ant-design/icons";

const QUICK_QUESTIONS = [
  "Summarize this meeting",
  "List key action items",
  "What decisions were made?",
];

interface WelcomeScreenProps {
  onQuickQuestion: (question: string) => void;
}

export default function WelcomeScreen({ onQuickQuestion }: WelcomeScreenProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100%",
        textAlign: "center",
        padding: "40px 20px",
      }}
    >
      <div
        style={{
          width: 80,
          height: 80,
          borderRadius: 24,
          background: "var(--gradient-primary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 24,
          boxShadow: "var(--glow-primary)",
        }}
      >
        <RobotOutlined style={{ fontSize: 36, color: "#fff" }} />
      </div>
      <h2
        style={{
          fontSize: 24,
          fontWeight: 700,
          margin: "0 0 8px",
          background: "var(--gradient-primary)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
        }}
      >
        How can I help you today?
      </h2>
      <p
        style={{
          color: "var(--color-text-tertiary)",
          marginBottom: 32,
          maxWidth: 400,
        }}
      >
        Ask me anything about your meetings. I can summarize discussions, find action items, and
        answer questions.
      </p>

      {/* Quick Questions */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: 10,
          width: "100%",
          maxWidth: 420,
        }}
      >
        {QUICK_QUESTIONS.map((question, idx) => (
          <motion.button
            key={question}
            type="button"
            aria-label={question}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: idx * 0.05 }}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={() => onQuickQuestion(question)}
            style={{
              padding: "10px 14px",
              borderRadius: 999,
              border: "1px solid var(--color-border)",
              background: "var(--color-bg-muted)",
              color: "var(--color-text-secondary)",
              fontSize: 14,
              cursor: "pointer",
              textAlign: "center",
              transition: "all 0.2s",
            }}
          >
            {question}
          </motion.button>
        ))}
      </div>
    </motion.div>
  );
}
