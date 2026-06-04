import React, { useEffect, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const emptySettings = {
  company_name: '',
  company_name_ar: '',
  address: '',
  phone: '',
  email: '',
  trn: '',
  license_number: '',
  footer_note: '',
  default_terms: '',
  payment_terms: 'Credit 30 days',
  validity_days: 30,
  prepared_by_default: '',
  signature_label: 'Signature',
  stamp_label: 'Stamp',
  pdf_template_style: 'classic',
  logo_layout: 'full_logo_only',
  primary_color: '#0F766E',
  accent_color: '#ECFDF5',
  show_arabic_name: true,
  show_trn: true,
  show_license_number: true,
  show_signature_area: true,
  show_stamp_area: true,
  ai_parsing_enabled: false,
  ai_auto_cleanup_enabled: false,
  ai_pdf_vision_enabled: false,
  ai_available: false,
  ai_unavailable_reason: '',
  ai_provider: '',
  ai_text_model: '',
  ai_vision_model: '',
  ai_global_enabled: true,
  logo_url: '',
  signature_image_url: '',
  stamp_image_url: '',
};

const booleanFields = new Set([
  'show_arabic_name',
  'show_trn',
  'show_license_number',
  'show_signature_area',
  'show_stamp_area',
  'ai_parsing_enabled',
  'ai_auto_cleanup_enabled',
  'ai_pdf_vision_enabled',
]);

const isValidHexColor = (value) => /^#[0-9A-Fa-f]{6}$/.test(value || '');

const BrandingImageInput = ({ title, imageUrl, selectedFile, emptyText, label, onChange, onRemove, removing, disabled }) => (
  <div className="qm-branding-upload">
    <div className="qm-branding-upload-header">
      <strong>{title}</strong>
      {selectedFile && <span className="qm-file-pill">{selectedFile.name}</span>}
    </div>
    {imageUrl ? (
      <div className="qm-logo-preview">
        <img src={imageUrl} alt={`${title} preview`} />
      </div>
    ) : (
      <div className="qm-empty compact">{emptyText}</div>
    )}
    <label><span className="qm-label-text">{label}</span>
      <input type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" disabled={disabled || removing} onChange={(event) => onChange(event.target.files?.[0] || null)} />
    </label>
    {imageUrl && (
      <button type="button" className="qm-secondary danger small" disabled={disabled || removing} onClick={onRemove}>
        {removing ? 'Removing...' : `Remove ${title}`}
      </button>
    )}
  </div>
);

const QuotationSettings = () => {
  const [settings, setSettings] = useState(emptySettings);
  const [logoFile, setLogoFile] = useState(null);
  const [signatureFile, setSignatureFile] = useState(null);
  const [stampFile, setStampFile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [clearingImage, setClearingImage] = useState('');
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.settings.retrieve();
      setSettings({ ...emptySettings, ...response.data });
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotation settings', 'GET /quotations/settings/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const updateField = (field, value) => {
    setNotice(null);
    setSettings((current) => ({ ...current, [field]: value }));
  };

  const saveSettings = async (event) => {
    event.preventDefault();
    if (saving) return;
    if (!isValidHexColor(settings.primary_color) || !isValidHexColor(settings.accent_color)) {
      setNotice({ type: 'error', message: 'Primary color and accent color must be valid hex values like #00796B.' });
      return;
    }
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const formData = new FormData();
      Object.entries(settings).forEach(([key, value]) => {
        if ([
          'id',
          'logo',
          'logo_url',
          'signature_image',
          'signature_image_url',
          'stamp_image',
          'stamp_image_url',
          'updated_by',
          'created_at',
          'updated_at',
          'ai_available',
          'ai_unavailable_reason',
          'ai_provider',
          'ai_text_model',
          'ai_vision_model',
          'ai_global_enabled',
        ].includes(key)) return;
        formData.append(key, booleanFields.has(key) ? (value ? 'true' : 'false') : (value ?? ''));
      });
      if (logoFile) formData.append('logo', logoFile);
      if (signatureFile) formData.append('signature_image', signatureFile);
      if (stampFile) formData.append('stamp_image', stampFile);
      const response = await quotationAPI.settings.update(formData, true);
      setSettings({ ...emptySettings, ...response.data });
      setLogoFile(null);
      setSignatureFile(null);
      setStampFile(null);
      setNotice({ type: 'success', message: 'Quotation settings saved.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save quotation settings', 'PATCH /quotations/settings/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const clearImage = async (field, title) => {
    if (saving || clearingImage) return;
    setClearingImage(field);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.settings.update({ [field]: true });
      setSettings({ ...emptySettings, ...response.data });
      if (field === 'clear_logo') setLogoFile(null);
      if (field === 'clear_signature_image') setSignatureFile(null);
      if (field === 'clear_stamp_image') setStampFile(null);
      setNotice({ type: 'success', message: `${title} removed.` });
    } catch (error) {
      const details = await describeQuotationError(error, `Remove ${title.toLowerCase()}`, 'PATCH /quotations/settings/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setClearingImage('');
    }
  };

  if (loading) return <div className="qm-loading">Loading quotation settings...</div>;

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {notice && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}
      <form className="qm-panel qm-settings-form" onSubmit={saveSettings}>
        <div className="qm-panel-heading">
          <div>
            <h3>Quotation Settings</h3>
            <p>These settings control the branding and default text used in generated quotation PDFs.</p>
          </div>
          <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Saving...' : 'Save Settings'}</button>
        </div>

        <div className="qm-settings-grid">
          <div className="qm-subpanel">
            <h4>Branding Images</h4>
            <BrandingImageInput
              title="Logo"
              imageUrl={settings.logo_url}
              selectedFile={logoFile}
              emptyText="No custom logo uploaded."
              label="Upload logo"
              onChange={setLogoFile}
              onRemove={() => clearImage('clear_logo', 'Logo')}
              removing={clearingImage === 'clear_logo'}
              disabled={saving || Boolean(clearingImage)}
            />
            <label><span className="qm-label-text">Logo layout</span>
              <select value={settings.logo_layout || 'full_logo_only'} onChange={(event) => updateField('logo_layout', event.target.value)}>
                <option value="full_logo_only">Full Logo Only</option>
                <option value="logo_plus_company_text">Logo + Company Text</option>
                <option value="icon_left_company_text">Icon Left + Company Text</option>
                <option value="no_logo">No Logo</option>
              </select>
            </label>
            <p className="qm-helper compact">If your uploaded logo already includes the company name, choose Full Logo Only. Use icon layouts only for icon-only marks.</p>
            <p className="qm-helper compact">Allowed: png, jpg, jpeg, webp. These images are PDF branding assets; quotation PDFs remain protected.</p>
          </div>

          <div className="qm-subpanel">
            <h4>Company Details</h4>
            <label><span className="qm-label-text">Company name</span><input value={settings.company_name} onChange={(event) => updateField('company_name', event.target.value)} /></label>
            <label><span className="qm-label-text">Arabic company name</span><input value={settings.company_name_ar || ''} onChange={(event) => updateField('company_name_ar', event.target.value)} /></label>
            <label><span className="qm-label-text">Address</span><textarea rows="3" value={settings.address || ''} onChange={(event) => updateField('address', event.target.value)} /></label>
            <div className="qm-grid-two">
              <label><span className="qm-label-text">Phone</span><input value={settings.phone || ''} onChange={(event) => updateField('phone', event.target.value)} /></label>
              <label><span className="qm-label-text">Email</span><input type="email" value={settings.email || ''} onChange={(event) => updateField('email', event.target.value)} /></label>
            </div>
            <div className="qm-grid-two">
              <label><span className="qm-label-text">TRN</span><input value={settings.trn || ''} onChange={(event) => updateField('trn', event.target.value)} /></label>
              <label><span className="qm-label-text">License number</span><input value={settings.license_number || ''} onChange={(event) => updateField('license_number', event.target.value)} /></label>
            </div>
          </div>
        </div>

        <div className="qm-settings-grid">
          <div className="qm-subpanel">
            <h4>Signature / Stamp</h4>
            <BrandingImageInput
              title="Signature"
              imageUrl={settings.signature_image_url}
              selectedFile={signatureFile}
              emptyText="No signature image uploaded."
              label="Upload signature"
              onChange={setSignatureFile}
              onRemove={() => clearImage('clear_signature_image', 'Signature')}
              removing={clearingImage === 'clear_signature_image'}
              disabled={saving || Boolean(clearingImage)}
            />
            <BrandingImageInput
              title="Stamp"
              imageUrl={settings.stamp_image_url}
              selectedFile={stampFile}
              emptyText="No stamp image uploaded."
              label="Upload stamp"
              onChange={setStampFile}
              onRemove={() => clearImage('clear_stamp_image', 'Stamp')}
              removing={clearingImage === 'clear_stamp_image'}
              disabled={saving || Boolean(clearingImage)}
            />
            <div className="qm-checkbox-stack">
              <label className="qm-checkbox"><input type="checkbox" checked={settings.show_signature_area} onChange={(event) => updateField('show_signature_area', event.target.checked)} /> Show signature area</label>
              <label className="qm-checkbox"><input type="checkbox" checked={settings.show_stamp_area} onChange={(event) => updateField('show_stamp_area', event.target.checked)} /> Show stamp area</label>
            </div>
          </div>

          <div className="qm-subpanel">
            <h4>PDF Text</h4>
            <label><span className="qm-label-text">Default terms</span><textarea rows="4" value={settings.default_terms || ''} onChange={(event) => updateField('default_terms', event.target.value)} /></label>
            <label><span className="qm-label-text">Payment terms</span><textarea rows="3" value={settings.payment_terms || ''} onChange={(event) => updateField('payment_terms', event.target.value)} /></label>
            <label><span className="qm-label-text">Footer note</span><textarea rows="2" value={settings.footer_note || ''} onChange={(event) => updateField('footer_note', event.target.value)} /></label>
            <div className="qm-grid-two">
              <label><span className="qm-label-text">Validity days</span><input type="number" min="1" max="365" value={settings.validity_days || 30} onChange={(event) => updateField('validity_days', event.target.value)} /></label>
              <label><span className="qm-label-text">Prepared by default</span><input value={settings.prepared_by_default || ''} onChange={(event) => updateField('prepared_by_default', event.target.value)} /></label>
            </div>
          </div>

          <div className="qm-subpanel">
            <h4>PDF Style</h4>
            <label><span className="qm-label-text">Template style</span>
              <select value={settings.pdf_template_style} onChange={(event) => updateField('pdf_template_style', event.target.value)}>
                <option value="classic">Classic</option>
                <option value="modern">Modern (reserved)</option>
                <option value="compact">Compact (reserved)</option>
              </select>
            </label>
            <div className="qm-grid-two">
              <label><span className="qm-label-text">Primary color</span><input type="text" value={settings.primary_color} onChange={(event) => updateField('primary_color', event.target.value)} placeholder="#00796B" /></label>
              <label><span className="qm-label-text">Accent color</span><input type="text" value={settings.accent_color} onChange={(event) => updateField('accent_color', event.target.value)} placeholder="#ECFDF5" /></label>
            </div>
            {(!isValidHexColor(settings.primary_color) || !isValidHexColor(settings.accent_color)) && (
              <p className="qm-helper error compact">Colors must be 6-digit hex values such as #00796B.</p>
            )}
            <div className="qm-grid-two">
              <label><span className="qm-label-text">Signature label</span><input value={settings.signature_label || ''} onChange={(event) => updateField('signature_label', event.target.value)} /></label>
              <label><span className="qm-label-text">Stamp label</span><input value={settings.stamp_label || ''} onChange={(event) => updateField('stamp_label', event.target.value)} /></label>
            </div>
            <label className="qm-checkbox"><input type="checkbox" checked={settings.show_arabic_name} onChange={(event) => updateField('show_arabic_name', event.target.checked)} /> Show Arabic name</label>
            <label className="qm-checkbox"><input type="checkbox" checked={settings.show_trn} onChange={(event) => updateField('show_trn', event.target.checked)} /> Show TRN</label>
            <label className="qm-checkbox"><input type="checkbox" checked={settings.show_license_number} onChange={(event) => updateField('show_license_number', event.target.checked)} /> Show license number</label>
          </div>

          <div className="qm-subpanel">
            <h4>AI Import Parsing</h4>
            <p className="qm-helper compact">
              AI cleanup is only used to clean messy extracted rows for staff review. It does not match Products, create aliases, create prices, or save imports by itself.
            </p>
            {!settings.ai_global_enabled && (
              <div className="qm-notice warning compact"><strong>AI unavailable:</strong> globally disabled by environment.</div>
            )}
            {settings.ai_global_enabled && !settings.ai_available && (
              <div className="qm-notice warning compact"><strong>AI unavailable:</strong> {settings.ai_unavailable_reason || 'missing API key or model configuration.'}</div>
            )}
            {settings.ai_available && !settings.ai_parsing_enabled && (
              <div className="qm-notice compact"><strong>AI disabled in settings.</strong> Turn on Enable AI Parsing to allow staff-triggered cleanup.</div>
            )}
            <div className="qm-checkbox-stack">
              <label className="qm-checkbox">
                <input
                  type="checkbox"
                  checked={settings.ai_parsing_enabled}
                  onChange={(event) => updateField('ai_parsing_enabled', event.target.checked)}
                />
                Enable AI Parsing
              </label>
              <label className="qm-checkbox">
                <input
                  type="checkbox"
                  checked={settings.ai_auto_cleanup_enabled}
                  disabled={!settings.ai_parsing_enabled}
                  onChange={(event) => updateField('ai_auto_cleanup_enabled', event.target.checked)}
                />
                Enable Auto AI Cleanup for weak deterministic parses
              </label>
              <label className="qm-checkbox">
                <input
                  type="checkbox"
                  checked={settings.ai_pdf_vision_enabled}
                  disabled={!settings.ai_parsing_enabled}
                  onChange={(event) => updateField('ai_pdf_vision_enabled', event.target.checked)}
                />
                Enable Vision AI for PDFs
              </label>
            </div>
            <div className="qm-meta-grid compact">
              <div className="qm-meta-item"><span>Provider</span><strong>{settings.ai_provider || '-'}</strong></div>
              <div className="qm-meta-item"><span>Text model</span><strong>{settings.ai_text_model || '-'}</strong></div>
              <div className="qm-meta-item"><span>Vision model</span><strong>{settings.ai_vision_model || '-'}</strong></div>
            </div>
            <p className="qm-helper compact">No AI API calls happen when AI Parsing is off. Missing Product matches never trigger AI cleanup.</p>
          </div>
        </div>
      </form>
    </div>
  );
};

export default QuotationSettings;
