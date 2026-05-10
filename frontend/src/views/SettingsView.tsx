import { useCallback, useEffect, useState } from "react";
import { Alert, App, Badge, Button, Form, Modal, Space, Spin, Tabs } from "antd";
import {
  CloudUploadOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { motion } from "framer-motion";
import { useIntl } from "react-intl";
import {
  formatApiErrorMessage,
  getAvailableBindings,
  getSettings,
  rebuildMultimodal,
  rebuildVectors,
  reloadConfig,
  updateSettings,
  type SettingsResponse,
} from "../api/client";
import { AiModelsTab } from "./settings/AiModelsTab";
import { isDeepEqual } from "./settings/constants";
import { IngestionTab } from "./settings/IngestionTab";
import { MemoryTab } from "./settings/MemoryTab";
import { RagTab } from "./settings/RagTab";
import { SearchUploadTab } from "./settings/SearchUploadTab";
import type { FormValues, SettingsBindings } from "./settings/types";
import { SystemTab } from "./settings/SystemTab";

export default function SettingsView() {
  const { formatMessage } = useIntl();
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const [originalValues, setOriginalValues] = useState<FormValues | null>(null);
  const [bindings, setBindings] = useState<SettingsBindings>({
    llm: [],
    embedding: [],
    search: [],
    reranker: [],
    tts: [],
    asr: [],
    ocr: [],
    vision: [],
  });
  const [activeTab, setActiveTab] = useState("models");
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildingMultimodal, setRebuildingMultimodal] = useState(false);
  const [reloading, setReloading] = useState(false);

  const fetchSettings = useCallback(async () => {
    setLoading(true);
    try {
      const [settingsRes, bindingsRes] = await Promise.all([getSettings(), getAvailableBindings()]);
      const data = settingsRes.data;
      const values: FormValues = {
        llm: data.llm,
        embedding: data.embedding,
        rag: data.rag,
        memory: data.memory,
        search: data.search,
        upload: data.upload,
        asr: data.asr,
        ocr: data.ocr,
        vision: data.vision,
        tts: data.tts,
        parser: data.parser,
        retention: data.retention,
        server: data.server,
      };
      form.setFieldsValue(values);
      setOriginalValues(values);
      setBindings(bindingsRes.data);
      setHasChanges(false);
    } catch (err) {
      message.error(formatApiErrorMessage(err, formatMessage({ id: "settings.loadedFailed" })));
    } finally {
      setLoading(false);
    }
  }, [form, formatMessage, message]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch sets state on completion, not during render
    fetchSettings();
  }, [fetchSettings]);

  const handleValuesChange = useCallback(() => {
    const current = form.getFieldsValue();
    setHasChanges(!originalValues || !isDeepEqual(current, originalValues));
  }, [form, originalValues]);

  const getChangedCategories = useCallback((): {
    needsVectorRebuild: boolean;
    needsMultimodalRebuild: boolean;
  } => {
    if (!originalValues) return { needsVectorRebuild: false, needsMultimodalRebuild: false };
    const current = form.getFieldsValue();
    const needsVectorRebuild =
      current.embedding?.binding !== originalValues.embedding?.binding ||
      current.embedding?.model !== originalValues.embedding?.model ||
      current.embedding?.dimension !== originalValues.embedding?.dimension;
    const multimodalFields = [
      "raganything_enabled",
      "raganything_fallback_to_native",
      "raganything_working_dir",
      "retriever_provider",
      "index_image_captions",
      "index_tables",
    ] as const;
    const needsMultimodalRebuild = multimodalFields.some(
      (f) => current.rag?.[f] !== originalValues.rag?.[f],
    );
    return { needsVectorRebuild, needsMultimodalRebuild };
  }, [form, originalValues]);

  const handleSave = async () => {
    const submitSettings = async (confirmVectorRebuild = false) => {
      const values = await form.validateFields();
      const changed: Partial<SettingsResponse> = {};
      if (originalValues) {
        if (!isDeepEqual(values.llm, originalValues.llm))
          changed.llm = { ...originalValues.llm, ...values.llm };
        if (!isDeepEqual(values.embedding, originalValues.embedding))
          changed.embedding = { ...originalValues.embedding, ...values.embedding };
        if (!isDeepEqual(values.rag, originalValues.rag))
          changed.rag = { ...originalValues.rag, ...values.rag };
        if (!isDeepEqual(values.memory, originalValues.memory))
          changed.memory = { ...originalValues.memory, ...values.memory };
        if (!isDeepEqual(values.search, originalValues.search))
          changed.search = { ...originalValues.search, ...values.search };
        if (!isDeepEqual(values.upload, originalValues.upload))
          changed.upload = { ...originalValues.upload, ...values.upload };
        if (!isDeepEqual(values.asr, originalValues.asr))
          changed.asr = { ...originalValues.asr, ...values.asr };
        if (!isDeepEqual(values.ocr, originalValues.ocr))
          changed.ocr = { ...originalValues.ocr, ...values.ocr };
        if (!isDeepEqual(values.vision, originalValues.vision))
          changed.vision = { ...originalValues.vision, ...values.vision };
        if (!isDeepEqual(values.tts, originalValues.tts))
          changed.tts = { ...originalValues.tts, ...values.tts };
        if (!isDeepEqual(values.parser, originalValues.parser))
          changed.parser = { ...originalValues.parser, ...values.parser };
        if (!isDeepEqual(values.retention, originalValues.retention))
          changed.retention = { ...originalValues.retention, ...values.retention };
      }
      await updateSettings({
        ...changed,
        confirm_vector_rebuild: confirmVectorRebuild,
      });
      setOriginalValues(values);
      setHasChanges(false);
    };

    let showingRebuildModal = false;
    try {
      setSaving(true);
      await submitSettings(false);
      const { needsVectorRebuild, needsMultimodalRebuild } = getChangedCategories();
      if (needsVectorRebuild) {
        message.success(formatMessage({ id: "settings.savedWithRebuildHint" }));
      } else if (needsMultimodalRebuild) {
        message.success(formatMessage({ id: "settings.savedMultimodalHint" }));
      } else {
        message.success(formatMessage({ id: "settings.savedImmediate" }));
      }
    } catch (err) {
      const msg = formatApiErrorMessage(err, formatMessage({ id: "settings.saveFailed" }));
      if (
        msg.includes("confirm_vector_rebuild=true") ||
        msg.includes("Embedding binding/model/dimension changed")
      ) {
        showingRebuildModal = true;
        Modal.confirm({
          title: formatMessage({ id: "settings.embeddingRebuild.title" }),
          content: formatMessage({ id: "settings.embeddingRebuild.content" }),
          okText: formatMessage({ id: "settings.embeddingRebuild.okText" }),
          cancelText: formatMessage({ id: "settings.reset" }),
          onOk: async () => {
            setSaving(true);
            try {
              await submitSettings(true);
              message.success(formatMessage({ id: "settings.savedWithRebuildHint" }));
            } catch (confirmErr) {
              message.error(
                formatApiErrorMessage(confirmErr, formatMessage({ id: "settings.saveFailed" })),
              );
            } finally {
              setSaving(false);
            }
          },
          onCancel: () => {
            setSaving(false);
          },
        });
      } else {
        message.error(msg);
      }
    } finally {
      if (!showingRebuildModal) {
        setSaving(false);
      }
    }
  };

  const handleReset = () => {
    if (!originalValues) return;
    form.setFieldsValue(originalValues);
    setHasChanges(false);
    message.info(formatMessage({ id: "settings.reverted" }));
  };

  const handleRebuildVectors = async () => {
    if (rebuilding) return;
    setRebuilding(true);
    try {
      await rebuildVectors();
      message.success(formatMessage({ id: "settings.rebuildStarted" }));
    } catch (err) {
      message.error(formatApiErrorMessage(err, formatMessage({ id: "settings.rebuildFailed" })));
    } finally {
      setRebuilding(false);
    }
  };

  const handleRebuildMultimodal = async () => {
    if (rebuildingMultimodal) return;
    setRebuildingMultimodal(true);
    try {
      await rebuildMultimodal();
      message.success(formatMessage({ id: "settings.rebuildMultimodalStarted" }));
    } catch (err) {
      message.error(
        formatApiErrorMessage(err, formatMessage({ id: "settings.rebuildMultimodalFailed" })),
      );
    } finally {
      setRebuildingMultimodal(false);
    }
  };

  const handleReloadConfig = async () => {
    setReloading(true);
    try {
      await reloadConfig();
      message.success(formatMessage({ id: "settings.reloadDone" }));
      await fetchSettings();
    } catch (err) {
      message.error(formatApiErrorMessage(err, formatMessage({ id: "settings.reloadFailed" })));
    } finally {
      setReloading(false);
    }
  };

  return (
    <div
      style={{
        padding: 24,
        background: "var(--color-bg-primary)",
        height: "calc(100vh - 72px)",
        overflow: "auto",
        maxWidth: 1000,
        margin: "0 auto",
      }}
    >
      <Form
        form={form}
        layout="vertical"
        onValuesChange={handleValuesChange}
        style={{ maxWidth: 1000 }}
      >
        {loading ? (
          <div
            style={{
              height: "calc(100vh - 72px)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Spin size="large" />
          </div>
        ) : (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, ease: "easeOut" }}
          >
            <Alert
              type="info"
              showIcon
              icon={<InfoCircleOutlined />}
              style={{ marginBottom: 16, borderRadius: 8 }}
              title={formatMessage({ id: "settings.memoryOnly.title" })}
              description={
                <span style={{ fontSize: 13 }}>
                  {formatMessage({ id: "settings.memoryOnly.desc" })}
                </span>
              }
            />
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "flex-end",
                marginBottom: 16,
              }}
            >
              <Space>
                {hasChanges && (
                  <Badge dot color="orange">
                    <span style={{ color: "var(--color-text-tertiary)", fontSize: 13 }}>
                      {formatMessage({ id: "settings.unsaved" })}
                    </span>
                  </Badge>
                )}
                <Button
                  icon={<ReloadOutlined />}
                  onClick={handleReset}
                  disabled={!hasChanges || saving}
                >
                  {formatMessage({ id: "settings.reset" })}
                </Button>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  onClick={handleSave}
                  loading={saving}
                  disabled={!hasChanges || saving}
                >
                  {formatMessage({ id: "settings.save" })}
                </Button>
              </Space>
            </div>

            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              type="card"
              style={{ marginBottom: 24 }}
              items={[
                {
                  key: "models",
                  label: (
                    <span>
                      <RobotOutlined style={{ marginRight: 8 }} />
                      {formatMessage({ id: "settings.tab.models" })}
                    </span>
                  ),
                  children: <AiModelsTab bindings={bindings} />,
                },
                {
                  key: "rag",
                  label: (
                    <span>
                      <FileTextOutlined style={{ marginRight: 8 }} />
                      {formatMessage({ id: "settings.tab.rag" })}
                    </span>
                  ),
                  children: <RagTab bindings={bindings} />,
                },
                {
                  key: "ingestion",
                  label: (
                    <span>
                      <ExperimentOutlined style={{ marginRight: 8 }} />
                      {formatMessage({ id: "settings.tab.ingestion" })}
                    </span>
                  ),
                  children: <IngestionTab />,
                },
                {
                  key: "memory",
                  label: (
                    <span>
                      <DatabaseOutlined style={{ marginRight: 8 }} />
                      {formatMessage({ id: "settings.tab.memory" })}
                    </span>
                  ),
                  children: <MemoryTab />,
                },
                {
                  key: "search-upload",
                  label: (
                    <span>
                      <CloudUploadOutlined style={{ marginRight: 8 }} />
                      {formatMessage({ id: "settings.tab.searchUpload" })}
                    </span>
                  ),
                  children: <SearchUploadTab bindings={bindings} />,
                },
                {
                  key: "system",
                  label: (
                    <span>
                      <SettingOutlined style={{ marginRight: 8 }} />
                      {formatMessage({ id: "settings.tab.system" })}
                    </span>
                  ),
                  children: (
                    <SystemTab
                      rebuilding={rebuilding}
                      rebuildingMultimodal={rebuildingMultimodal}
                      reloading={reloading}
                      onRebuildVectors={handleRebuildVectors}
                      onRebuildMultimodal={handleRebuildMultimodal}
                      onReloadConfig={handleReloadConfig}
                    />
                  ),
                },
              ]}
            />
          </motion.div>
        )}
      </Form>
    </div>
  );
}
