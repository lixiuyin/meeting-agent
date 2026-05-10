import { Card, Collapse, Divider, Form, Input, InputNumber, Select, Tooltip } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import { cardVariants, settingsCardStyle, twoColGrid } from "./constants";
import type { SettingsBindings } from "./types";
import SettingSwitch from "./SettingSwitch";
import { RerankerCard } from "./RerankerCard";
import { ChunkingStrategyCard } from "./ChunkingStrategyCard";

interface Props {
  bindings: SettingsBindings;
}

export function RagTab({ bindings }: Props) {
  const { formatMessage } = useIntl();

  return (
    <motion.div custom={0} variants={cardVariants} initial="hidden" animate="visible">
      <Card
        title={formatMessage({ id: "settings.rag.title" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <div style={twoColGrid}>
          <Form.Item
            name={["rag", "chunk_size"]}
            label={formatMessage({ id: "settings.rag.chunkSize" })}
            rules={[{ required: true }]}
          >
            <InputNumber min={256} max={8192} step={128} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["rag", "chunk_overlap"]}
            label={formatMessage({ id: "settings.rag.chunkOverlap" })}
            rules={[{ required: true }]}
          >
            <InputNumber min={0} max={2048} step={32} style={{ width: "100%" }} />
          </Form.Item>
        </div>

        <div style={twoColGrid}>
          <Form.Item
            name={["rag", "top_k"]}
            label={formatMessage({ id: "settings.rag.topK" })}
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={50} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name={["rag", "score_threshold"]}
            label={
              <span>
                {formatMessage({ id: "settings.rag.scoreThreshold" })}
                <Tooltip title={formatMessage({ id: "settings.rag.scoreThresholdTip" })}>
                  <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.6 }} />
                </Tooltip>
              </span>
            }
            rules={[{ required: true }]}
          >
            <InputNumber min={0} max={10} step={0.1} style={{ width: "100%" }} />
          </Form.Item>
        </div>

        <Divider style={{ borderColor: "var(--color-border)" }} />
        <Form.Item
          name={["rag", "query_rewrite_enabled"]}
          valuePropName="checked"
          style={{ marginBottom: 8 }}
        >
          <SettingSwitch label={formatMessage({ id: "settings.rag.queryRewriting" })} />
        </Form.Item>
        <Form.Item
          name={["rag", "hybrid_search_enabled"]}
          valuePropName="checked"
          style={{ marginBottom: 16 }}
        >
          <SettingSwitch label={formatMessage({ id: "settings.rag.hybridSearch" })} />
        </Form.Item>

        <Form.Item
          noStyle
          shouldUpdate={(prev, curr) =>
            prev.rag?.hybrid_search_enabled !== curr.rag?.hybrid_search_enabled
          }
        >
          {({ getFieldValue }) =>
            getFieldValue(["rag", "hybrid_search_enabled"]) ? (
              <Form.Item
                name={["rag", "hybrid_alpha"]}
                label={formatMessage({ id: "settings.rag.hybridAlpha" })}
              >
                <InputNumber
                  min={0}
                  max={1}
                  step={0.1}
                  style={{ width: "100%" }}
                  placeholder="0 = pure vector, 1 = pure BM25"
                />
              </Form.Item>
            ) : null
          }
        </Form.Item>

        <Divider style={{ borderColor: "var(--color-border)" }} />
        <Form.Item
          name={["rag", "retriever_provider"]}
          label={
            <span>
              {formatMessage({ id: "settings.rag.retrievalProvider" })}
              <Form.Item
                noStyle
                shouldUpdate={(prev, curr) =>
                  prev.rag?.retriever_provider !== curr.rag?.retriever_provider
                }
              >
                {({ getFieldValue }) => {
                  const val = getFieldValue(["rag", "retriever_provider"]);
                  if (val === "multimodal" || val === "hybrid_multimodal") {
                    return (
                      <Tooltip title={formatMessage({ id: "settings.savedMultimodalHint" })}>
                        <QuestionCircleOutlined style={{ marginLeft: 8, opacity: 0.5 }} />
                      </Tooltip>
                    );
                  }
                  return null;
                }}
              </Form.Item>
            </span>
          }
          rules={[{ required: true }]}
        >
          <Select
            options={[
              { value: "native", label: "Native" },
              { value: "hybrid", label: "Hybrid" },
              { value: "multimodal", label: "Multimodal" },
              { value: "hybrid_multimodal", label: "Hybrid Multimodal" },
            ]}
          />
        </Form.Item>

        <Form.Item
          name={["rag", "distance_metric"]}
          label={formatMessage({ id: "settings.rag.distanceMetric" })}
        >
          <Select
            options={[
              { value: "l2", label: "L2 (Euclidean)" },
              { value: "cosine", label: "Cosine" },
              { value: "ip", label: "Inner Product" },
            ]}
          />
        </Form.Item>
      </Card>

      <RerankerCard bindings={bindings} />
      <ChunkingStrategyCard />

      <Collapse
        ghost
        items={[
          {
            key: "advanced",
            label: formatMessage({ id: "settings.rag.advancedOptions" }),
            children: (
              <>
                {/* Semantic & Multi-Query */}
                <Divider style={{ borderColor: "var(--color-border)", margin: "12px 0" }} />
                <Form.Item
                  name={["rag", "semantic_chunking_enabled"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.semanticChunking" })}
                  />
                </Form.Item>
                <Form.Item
                  name={["rag", "multi_query_enabled"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.multiQuery" })}
                  />
                </Form.Item>
                <Form.Item
                  noStyle
                  shouldUpdate={(prev, curr) =>
                    prev.rag?.multi_query_enabled !== curr.rag?.multi_query_enabled
                  }
                >
                  {({ getFieldValue }) =>
                    getFieldValue(["rag", "multi_query_enabled"]) ? (
                      <div style={twoColGrid}>
                        <Form.Item
                          name={["rag", "multi_query_count"]}
                          label={formatMessage({ id: "settings.rag.queryVariants" })}
                        >
                          <InputNumber min={1} max={10} style={{ width: "100%" }} />
                        </Form.Item>
                        <Form.Item
                          name={["rag", "query_rewrite_model"]}
                          label={formatMessage({ id: "settings.rag.rewriteModel" })}
                        >
                          <Input placeholder="Empty = same as LLM" />
                        </Form.Item>
                      </div>
                    ) : null
                  }
                </Form.Item>

                {/* RAGAnything */}
                <Divider style={{ borderColor: "var(--color-border)", margin: "12px 0" }} />
                <Form.Item
                  name={["rag", "raganything_enabled"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.raganything" })}
                    tooltip={formatMessage({ id: "settings.models.embeddingChangeWarning" })}
                  />
                </Form.Item>
                <Form.Item
                  name={["rag", "raganything_fallback_to_native"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.raganythingFallback" })}
                  />
                </Form.Item>
                <Form.Item
                  name={["rag", "raganything_working_dir"]}
                  label={formatMessage({ id: "settings.rag.raganythingWorkingDir" })}
                >
                  <Input placeholder="" />
                </Form.Item>
                <div style={twoColGrid}>
                  <Form.Item
                    name={["rag", "raganything_index_timeout_seconds"]}
                    label={formatMessage({ id: "settings.rag.indexTimeout" })}
                  >
                    <InputNumber min={10} max={600} step={10} style={{ width: "100%" }} />
                  </Form.Item>
                  <Form.Item
                    name={["rag", "raganything_query_timeout_seconds"]}
                    label={formatMessage({ id: "settings.rag.queryTimeout" })}
                  >
                    <InputNumber min={5} max={300} step={5} style={{ width: "100%" }} />
                  </Form.Item>
                </div>
                <Form.Item
                  name={["rag", "raganything_llm_timeout_seconds"]}
                  label={formatMessage({ id: "settings.rag.llmTimeout" })}
                >
                  <InputNumber min={10} max={600} step={10} style={{ width: "100%" }} />
                </Form.Item>

                {/* Index Options */}
                <Divider style={{ borderColor: "var(--color-border)", margin: "12px 0" }} />
                <Form.Item
                  name={["rag", "index_tables"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.indexTables" })}
                  />
                </Form.Item>
                <Form.Item
                  name={["rag", "index_image_captions"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.indexImageCaptions" })}
                  />
                </Form.Item>
                <Form.Item
                  name={["rag", "image_ocr_min_length"]}
                  label={formatMessage({ id: "settings.rag.imageOcrMinLength" })}
                >
                  <InputNumber min={1} max={200} style={{ width: "100%" }} />
                </Form.Item>
                <Form.Item
                  name={["rag", "content_type_rerank_enabled"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.contentTypeRerank" })}
                  />
                </Form.Item>

                {/* Sibling Coretrieve */}
                <Divider style={{ borderColor: "var(--color-border)", margin: "12px 0" }} />
                <Form.Item
                  name={["rag", "sibling_coretrieve_enabled"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.siblingCoretrieve" })}
                  />
                </Form.Item>
                <div style={twoColGrid}>
                  <Form.Item
                    name={["rag", "sibling_coretrieve_per_anchor"]}
                    label={formatMessage({ id: "settings.rag.siblingsPerAnchor" })}
                  >
                    <InputNumber min={1} max={10} style={{ width: "100%" }} />
                  </Form.Item>
                  <Form.Item
                    name={["rag", "sibling_coretrieve_max_total"]}
                    label={formatMessage({ id: "settings.rag.maxSiblingResults" })}
                  >
                    <InputNumber min={1} max={20} style={{ width: "100%" }} />
                  </Form.Item>
                </div>

                {/* Audio Semantic */}
                <Divider style={{ borderColor: "var(--color-border)", margin: "12px 0" }} />
                <Form.Item
                  name={["rag", "audio_semantic_boundary_enabled"]}
                  valuePropName="checked"
                  style={{ marginBottom: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.audioSemanticBoundary" })}
                  />
                </Form.Item>
                <Form.Item
                  noStyle
                  shouldUpdate={(prev, curr) =>
                    prev.rag?.audio_semantic_boundary_enabled !==
                    curr.rag?.audio_semantic_boundary_enabled
                  }
                >
                  {({ getFieldValue }) =>
                    getFieldValue(["rag", "audio_semantic_boundary_enabled"]) ? (
                      <>
                        <div style={twoColGrid}>
                          <Form.Item
                            name={["rag", "audio_semantic_boundary_threshold"]}
                            label={formatMessage({ id: "settings.rag.boundaryThreshold" })}
                          >
                            <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                          </Form.Item>
                          <Form.Item
                            name={["rag", "audio_semantic_min_segments"]}
                            label={formatMessage({ id: "settings.rag.minSegments" })}
                          >
                            <InputNumber min={1} max={50} style={{ width: "100%" }} />
                          </Form.Item>
                        </div>
                        <Form.Item
                          name={["rag", "audio_semantic_max_segments"]}
                          label={formatMessage({ id: "settings.rag.maxSegments" })}
                        >
                          <InputNumber min={2} max={200} style={{ width: "100%" }} />
                        </Form.Item>
                      </>
                    ) : null
                  }
                </Form.Item>
                <Form.Item
                  name={["rag", "speaker_in_content"]}
                  valuePropName="checked"
                  style={{ marginBottom: 0 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.speakerInContent" })}
                  />
                </Form.Item>
                <Form.Item
                  name={["rag", "split_on_speaker_change"]}
                  valuePropName="checked"
                  style={{ marginBottom: 0, marginTop: 8 }}
                >
                  <SettingSwitch
                    size="small"
                    label={formatMessage({ id: "settings.rag.splitOnSpeakerChange" })}
                  />
                </Form.Item>
              </>
            ),
          },
        ]}
      />
    </motion.div>
  );
}
