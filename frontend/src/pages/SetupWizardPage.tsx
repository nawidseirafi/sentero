import { SetupWizard } from '../components/SetupWizard';

export function SetupWizardPage({ onFinish }: { onFinish: () => void }) {
  return <SetupWizard onFinish={onFinish} />;
}
