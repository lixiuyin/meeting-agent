import { Card, Collapse, Form, InputNumber, Select, Switch } from "antd";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import { cardVariants, settingsCardStyle, twoColGrid } from "./constants";

export function MemoryTab() {
  const { formatMessage } = useIntl();

  return (
    <motion.div custom={0} variants={cardVariants} initial="hidden" animate="visible">
      <Card
        title={formatMessage({ id: "settings.memory.operatingModes" })}
        style={{ ...settingsCardStyle, marginBottom: 16 }}
      >
        <div style={{ color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
          {formatMessage({ id: "settings.memory.operatingModesDescription" })}
        </div>
      </Card>

      <Collapse
        items={[
          {
            key: "expert-memory",
            label: formatMessage({ id: "settings.memory.expertConfiguration" }),
            children: (
              <>
                <Card
                  title={formatMessage({ id: "settings.memory.core" })}
                  style={{ ...settingsCardStyle, marginBottom: 16 }}
                >
                  <Form.Item
                    name={["memory", "auto_extract"]}
                    valuePropName="checked"
                    style={{ marginBottom: 16 }}
                  >
                    <div style={{ display: "flex", alignItems: "center" }}>
                      <Switch
                        checkedChildren={formatMessage({ id: "common.enabled" })}
                        unCheckedChildren={formatMessage({ id: "common.disabled" })}
                      />
                      <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
                        {formatMessage({ id: "settings.memory.autoExtract" })}
                      </span>
                    </div>
                  </Form.Item>

                  <div style={twoColGrid}>
                    <Form.Item
                      name={["memory", "max_facts_per_turn"]}
                      label={formatMessage({ id: "settings.memory.maxFactsPerTurn" })}
                      rules={[{ required: true }]}
                    >
                      <InputNumber min={1} max={10} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      name={["memory", "session_max_history"]}
                      label={formatMessage({ id: "settings.memory.sessionHistory" })}
                      rules={[{ required: true }]}
                    >
                      <InputNumber min={1} max={200} style={{ width: "100%" }} />
                    </Form.Item>
                  </div>

                  <Form.Item
                    name={["memory", "decay_enabled"]}
                    valuePropName="checked"
                    style={{ marginBottom: 16 }}
                  >
                    <div style={{ display: "flex", alignItems: "center" }}>
                      <Switch
                        checkedChildren={formatMessage({ id: "common.enabled" })}
                        unCheckedChildren={formatMessage({ id: "common.disabled" })}
                      />
                      <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
                        {formatMessage({ id: "settings.memory.decay" })}
                      </span>
                    </div>
                  </Form.Item>

                  <div style={twoColGrid}>
                    <Form.Item
                      name={["memory", "ttl_days"]}
                      label={formatMessage({ id: "settings.memory.ttlDays" })}
                      rules={[{ required: true }]}
                    >
                      <InputNumber min={1} max={365} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      name={["memory", "decay_interval_hours"]}
                      label={formatMessage({ id: "settings.memory.decayInterval" })}
                    >
                      <InputNumber min={1} max={168} step={6} style={{ width: "100%" }} />
                    </Form.Item>
                  </div>

                  <div style={twoColGrid}>
                    <Form.Item
                      name={["memory", "max_context_items"]}
                      label={formatMessage({ id: "settings.memory.maxContextItems" })}
                    >
                      <InputNumber min={1} max={20} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      name={["memory", "global_memory_limit"]}
                      label={formatMessage({ id: "settings.memory.globalLimit" })}
                    >
                      <InputNumber min={0} max={20} style={{ width: "100%" }} />
                    </Form.Item>
                  </div>

                  <div style={twoColGrid}>
                    <Form.Item
                      name={["memory", "skip_threshold"]}
                      label={formatMessage({ id: "settings.memory.skipThreshold" })}
                    >
                      <InputNumber min={1} max={20} style={{ width: "100%" }} />
                    </Form.Item>
                    <Form.Item
                      name={["memory", "entity_relations_limit"]}
                      label={formatMessage({ id: "settings.memory.entityRelationsLimit" })}
                    >
                      <InputNumber min={5} max={500} step={5} style={{ width: "100%" }} />
                    </Form.Item>
                  </div>

                  <Form.Item
                    name={["memory", "extraction_mode"]}
                    label={formatMessage({ id: "settings.memory.extractionMode" })}
                  >
                    <Select
                      options={[
                        { value: "precise", label: "Precise" },
                        { value: "balanced", label: "Balanced" },
                        { value: "aggressive", label: "Aggressive" },
                      ]}
                    />
                  </Form.Item>
                </Card>

                <Collapse
                  ghost
                  items={[
                    {
                      key: "session",
                      label: formatMessage({ id: "settings.memory.sessionSummary" }),
                      children: (
                        <>
                          <Form.Item
                            name={["memory", "session_summary_enabled"]}
                            valuePropName="checked"
                            style={{ marginBottom: 8 }}
                          >
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <Switch
                                checkedChildren={formatMessage({ id: "common.enabled" })}
                                unCheckedChildren={formatMessage({ id: "common.disabled" })}
                                size="small"
                              />
                              <span
                                style={{
                                  marginLeft: 8,
                                  color: "var(--color-text-secondary)",
                                  fontSize: 13,
                                }}
                              >
                                {formatMessage({ id: "settings.memory.autoSummary" })}
                              </span>
                            </div>
                          </Form.Item>
                          <div style={twoColGrid}>
                            <Form.Item
                              name={["memory", "session_summary_min_turns"]}
                              label={formatMessage({ id: "settings.memory.minTurns" })}
                            >
                              <InputNumber min={2} max={20} style={{ width: "100%" }} />
                            </Form.Item>
                            <Form.Item
                              name={["memory", "session_summary_max_items"]}
                              label={formatMessage({ id: "settings.memory.maxSummaries" })}
                            >
                              <InputNumber min={1} max={10} style={{ width: "100%" }} />
                            </Form.Item>
                          </div>
                          <div style={twoColGrid}>
                            <Form.Item
                              name={["memory", "session_summary_max_messages"]}
                              label={formatMessage({ id: "settings.memory.maxMessages" })}
                            >
                              <InputNumber
                                min={10}
                                max={1000}
                                step={10}
                                style={{ width: "100%" }}
                              />
                            </Form.Item>
                            <Form.Item
                              name={["memory", "session_summary_idle_minutes"]}
                              label={formatMessage({ id: "settings.memory.idleMinutes" })}
                            >
                              <InputNumber min={5} max={120} step={5} style={{ width: "100%" }} />
                            </Form.Item>
                          </div>
                          <Form.Item
                            name={["memory", "session_summary_startup_backfill"]}
                            valuePropName="checked"
                            style={{ marginBottom: 8 }}
                          >
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <Switch
                                checkedChildren={formatMessage({ id: "common.enabled" })}
                                unCheckedChildren={formatMessage({ id: "common.disabled" })}
                                size="small"
                              />
                              <span
                                style={{
                                  marginLeft: 8,
                                  color: "var(--color-text-secondary)",
                                  fontSize: 13,
                                }}
                              >
                                {formatMessage({ id: "settings.memory.startupBackfill" })}
                              </span>
                            </div>
                          </Form.Item>
                          <div style={twoColGrid}>
                            <Form.Item
                              name={["memory", "session_max_tokens"]}
                              label={formatMessage({ id: "settings.memory.sessionTokenBudget" })}
                            >
                              <InputNumber
                                min={512}
                                max={128000}
                                step={512}
                                style={{ width: "100%" }}
                              />
                            </Form.Item>
                          </div>
                        </>
                      ),
                    },
                    {
                      key: "advanced",
                      label: formatMessage({ id: "settings.memory.advanced" }),
                      children: (
                        <>
                          <Form.Item
                            name={["memory", "consolidation_enabled"]}
                            valuePropName="checked"
                            style={{ marginBottom: 8 }}
                          >
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <Switch
                                checkedChildren={formatMessage({ id: "common.enabled" })}
                                unCheckedChildren={formatMessage({ id: "common.disabled" })}
                                size="small"
                              />
                              <span
                                style={{
                                  marginLeft: 8,
                                  color: "var(--color-text-secondary)",
                                  fontSize: 13,
                                }}
                              >
                                {formatMessage({ id: "settings.memory.consolidation" })}
                              </span>
                            </div>
                          </Form.Item>
                          <Form.Item
                            name={["memory", "consolidation_min_cluster"]}
                            label={formatMessage({ id: "settings.memory.minCluster" })}
                          >
                            <InputNumber min={2} max={20} style={{ width: "100%" }} />
                          </Form.Item>

                          <Form.Item
                            name={["memory", "semantic_cluster_enabled"]}
                            valuePropName="checked"
                            style={{ marginBottom: 8 }}
                          >
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <Switch
                                checkedChildren={formatMessage({ id: "common.enabled" })}
                                unCheckedChildren={formatMessage({ id: "common.disabled" })}
                                size="small"
                              />
                              <span
                                style={{
                                  marginLeft: 8,
                                  color: "var(--color-text-secondary)",
                                  fontSize: 13,
                                }}
                              >
                                {formatMessage({ id: "settings.memory.semanticCluster" })}
                              </span>
                            </div>
                          </Form.Item>

                          <Form.Item
                            name={["memory", "knowledge_graph_enabled"]}
                            valuePropName="checked"
                            style={{ marginBottom: 8 }}
                          >
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <Switch
                                checkedChildren={formatMessage({ id: "common.enabled" })}
                                unCheckedChildren={formatMessage({ id: "common.disabled" })}
                                size="small"
                              />
                              <span
                                style={{
                                  marginLeft: 8,
                                  color: "var(--color-text-secondary)",
                                  fontSize: 13,
                                }}
                              >
                                {formatMessage({ id: "settings.memory.knowledgeGraph" })}
                              </span>
                            </div>
                          </Form.Item>

                          <Form.Item
                            name={["memory", "profile_enabled"]}
                            valuePropName="checked"
                            style={{ marginBottom: 8 }}
                          >
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <Switch
                                checkedChildren={formatMessage({ id: "common.enabled" })}
                                unCheckedChildren={formatMessage({ id: "common.disabled" })}
                                size="small"
                              />
                              <span
                                style={{
                                  marginLeft: 8,
                                  color: "var(--color-text-secondary)",
                                  fontSize: 13,
                                }}
                              >
                                {formatMessage({ id: "settings.memory.profileRefresh" })}
                              </span>
                            </div>
                          </Form.Item>
                          <Form.Item
                            name={["memory", "profile_refresh_interval"]}
                            label={formatMessage({ id: "settings.memory.profileRefreshInterval" })}
                          >
                            <InputNumber min={10} max={500} step={10} style={{ width: "100%" }} />
                          </Form.Item>
                        </>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
        ]}
      />
    </motion.div>
  );
}
