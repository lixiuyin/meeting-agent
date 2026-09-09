import { Form, Input, Modal } from "antd";
import { useIntl } from "react-intl";
import type { CreateSkillFormValues } from "./skillDisplay";
import { normalizeSkillName } from "./skillDisplay";

interface CreateSkillModalProps {
  open: boolean;
  confirmLoading: boolean;
  onCancel: () => void;
  onSubmit: (values: CreateSkillFormValues) => Promise<void>;
}

export function CreateSkillModal({
  open,
  confirmLoading,
  onCancel,
  onSubmit,
}: CreateSkillModalProps) {
  const [form] = Form.useForm<CreateSkillFormValues>();
  const { formatMessage } = useIntl();

  const handleCancel = () => {
    form.resetFields();
    onCancel();
  };

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      await onSubmit(values);
      form.resetFields();
    } catch (error) {
      // Ant Design rejects validateFields for ordinary validation failures.
      // The fields already display those errors; swallowing only that shape
      // avoids an unhandled promise rejection in the browser console.
      if (typeof error === "object" && error !== null && "errorFields" in error) return;
      throw error;
    }
  };

  return (
    <Modal
      title={formatMessage({ id: "generation.createModal.title" })}
      open={open}
      onCancel={handleCancel}
      onOk={handleOk}
      okText={formatMessage({ id: "generation.createModal.okText" })}
      confirmLoading={confirmLoading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          requiredKeywords: "",
          optionalKeywords: "",
          examples: "",
        }}
      >
        <Form.Item
          label={formatMessage({ id: "generation.createModal.skillName" })}
          name="name"
          rules={[
            {
              required: true,
              message: formatMessage({ id: "generation.createModal.skillNameRequired" }),
            },
            () => ({
              validator(_, value: string) {
                const normalized = normalizeSkillName(value || "");
                if (!/^[a-z][a-z0-9_]{2,62}$/.test(normalized)) {
                  return Promise.reject(
                    new Error(formatMessage({ id: "generation.createModal.skillNameInvalid" })),
                  );
                }
                return Promise.resolve();
              },
            }),
          ]}
          extra={formatMessage({ id: "generation.createModal.skillNameExtra" })}
        >
          <Input
            placeholder={formatMessage({ id: "generation.createModal.skillNamePlaceholder" })}
          />
        </Form.Item>
        <Form.Item
          label={formatMessage({ id: "generation.createModal.displayName" })}
          name="displayName"
          rules={[
            {
              required: true,
              message: formatMessage({ id: "generation.createModal.displayNameRequired" }),
            },
          ]}
        >
          <Input
            placeholder={formatMessage({ id: "generation.createModal.displayNamePlaceholder" })}
          />
        </Form.Item>
        <Form.Item
          label={formatMessage({ id: "generation.createModal.description" })}
          name="description"
          rules={[
            {
              required: true,
              message: formatMessage({ id: "generation.createModal.descriptionRequired" }),
            },
            { min: 10, message: formatMessage({ id: "generation.createModal.descriptionMin" }) },
          ]}
        >
          <Input.TextArea
            rows={3}
            placeholder={formatMessage({ id: "generation.createModal.descriptionPlaceholder" })}
          />
        </Form.Item>
        <Form.Item
          label={formatMessage({ id: "generation.createModal.requiredKeywords" })}
          name="requiredKeywords"
        >
          <Input
            placeholder={formatMessage({
              id: "generation.createModal.requiredKeywordsPlaceholder",
            })}
          />
        </Form.Item>
        <Form.Item
          label={formatMessage({ id: "generation.createModal.optionalKeywords" })}
          name="optionalKeywords"
        >
          <Input
            placeholder={formatMessage({
              id: "generation.createModal.optionalKeywordsPlaceholder",
            })}
          />
        </Form.Item>
        <Form.Item label={formatMessage({ id: "generation.createModal.examples" })} name="examples">
          <Input.TextArea
            rows={3}
            placeholder={formatMessage({ id: "generation.createModal.examplesPlaceholder" })}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
