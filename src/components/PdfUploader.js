import React, { useState } from "react";
import {
  FilePdfOutlined,
  InboxOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { Button, message, Tag, Tooltip, Upload } from "antd";
import { uploadPdf } from "../api";

const { Dragger } = Upload;

const isPdf = (file) =>
  file.type === "application/pdf" || file.name?.toLowerCase().endsWith(".pdf");

/**
 * Full drag-and-drop area when no document is loaded;
 * collapses to a compact "active document" bar once one is.
 */
const PdfUploader = ({ activeDoc, onUploaded }) => {
  const [uploading, setUploading] = useState(false);

  const props = {
    name: "file",
    accept: ".pdf,application/pdf",
    multiple: false,
    maxCount: 1,
    showUploadList: false,
    beforeUpload: (file) => {
      if (!isPdf(file)) {
        message.error(`${file.name} is not a PDF.`);
        return Upload.LIST_IGNORE;
      }
      return true;
    },
    customRequest: async ({ file, onSuccess, onError }) => {
      setUploading(true);
      const hide = message.loading(`Uploading ${file.name}…`, 0);
      try {
        const res = await uploadPdf(file);
        onSuccess(res.data);
        message.success(`${file.name} is ready. Ask away!`);
        onUploaded?.(file.name);
      } catch (e) {
        onError(e);
        message.error(`${file.name}: ${e.message}`);
      } finally {
        hide();
        setUploading(false);
      }
    },
  };

  if (activeDoc) {
    return (
      <div className="doc-bar">
        <span className="doc-bar-label">Answering from</span>
        <Tag icon={<FilePdfOutlined />} color="purple" className="doc-chip">
          {activeDoc}
        </Tag>
        <Upload {...props}>
          <Tooltip title="Replace the current PDF">
            <Button
              size="small"
              icon={<SwapOutlined />}
              loading={uploading}
              aria-label="Change document"
            >
              Change document
            </Button>
          </Tooltip>
        </Upload>
      </div>
    );
  }

  return (
    <Dragger {...props} disabled={uploading}>
      <p className="ant-upload-drag-icon">
        <InboxOutlined />
      </p>
      <p className="ant-upload-text">
        {uploading ? "Uploading…" : "Click or drag a PDF here to get started"}
      </p>
      <p className="ant-upload-hint">
        One PDF at a time. AskScout answers your questions from it. Don't
        upload confidential documents.
      </p>
    </Dragger>
  );
};

export default PdfUploader;
