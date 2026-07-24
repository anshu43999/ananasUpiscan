import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { createTask, submitCheckout, fetchCapacity } from '../api/tasks';
import { CapacityBar } from '../components/CapacityBar';
import type { PaymentMethod, CapacityInfo } from '../types';

interface Props {
  onCreated: (taskId: string) => void;
  onBack: () => void;
}

const step1Schema = z.object({
  payment_method: z.enum(['upi', 'pix'], { error: '请选择支付方式' }),
  account_email: z.string().email('请输入有效的邮箱地址'),
  markup_cny: z.coerce.number().min(0, '不能为负数').default(0),
});

const step2Schema = z.object({
  access_token: z.string().min(1, '请输入 access_token'),
  pay_link: z.string().optional(),
  brcode: z.string().optional(),
});

type Step2Data = z.infer<typeof step2Schema>;

export function NewTask({ onCreated, onBack }: Props) {
  const [step, setStep] = useState(1);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [capacity, setCapacity] = useState<CapacityInfo | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form1 = useForm<z.input<typeof step1Schema>>({
    resolver: zodResolver(step1Schema),
    defaultValues: { payment_method: 'upi', account_email: '', markup_cny: 0 },
  });

  const form2 = useForm<z.input<typeof step2Schema>>({
    resolver: zodResolver(step2Schema),
    defaultValues: { access_token: '', pay_link: '', brcode: '' },
  });

  const paymentMethod = form1.watch('payment_method');

  const handleStep1 = async (data: z.input<typeof step1Schema>) => {
    setSubmitting(true);
    setError(null);

    try {
      const cap = await fetchCapacity(data.payment_method);
      setCapacity(cap);

      if (!cap.accepting_new) {
        setError('当前队列已满，暂时无法接收新任务，请稍后再试');
        setSubmitting(false);
        return;
      }

      const task = await createTask({
        payment_method: data.payment_method,
        account_email: data.account_email,
        markup_cny: data.markup_cny as number,
      });

      setTaskId(task.id);
      setStep(2);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '创建任务失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleStep2 = async (data: Step2Data) => {
    if (!taskId) return;
    setSubmitting(true);
    setError(null);

    try {
      const checkoutData: Record<string, string> = {
        access_token: data.access_token,
      };
      if (paymentMethod === 'upi' && data.pay_link) {
        checkoutData.pay_link = data.pay_link;
      } else if (paymentMethod === 'pix' && data.brcode) {
        checkoutData.brcode = data.brcode;
      }

      await submitCheckout(taskId, { checkout_data: checkoutData as any });
      onCreated(taskId);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '提交 checkout 失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-2xl">
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={onBack}
          aria-label="返回任务列表"
          className="text-gray-400 hover:text-gray-600 transition-colors"
        >
          <svg className="w-5 h-5" aria-hidden="true" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h2 className="text-xl font-semibold text-gray-900">提交账号</h2>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-4 mb-8">
        <div className={`flex items-center gap-2 ${step >= 1 ? 'text-purple-700' : 'text-gray-400'}`}>
          <span className={`w-7 h-7 rounded-full flex items-center justify-center text-sm font-medium ${
            step >= 1 ? 'bg-purple-600 text-white' : 'bg-gray-200 text-gray-500'
          }`}>1</span>
          <span className="text-sm font-medium">基本信息</span>
        </div>
        <div className="flex-1 h-px bg-gray-200" />
        <div className={`flex items-center gap-2 ${step >= 2 ? 'text-purple-700' : 'text-gray-400'}`}>
          <span className={`w-7 h-7 rounded-full flex items-center justify-center text-sm font-medium ${
            step >= 2 ? 'bg-purple-600 text-white' : 'bg-gray-200 text-gray-500'
          }`}>2</span>
          <span className="text-sm font-medium">提交 Checkout</span>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 text-sm rounded-lg px-4 py-3 mb-4">
          {error}
        </div>
      )}

      {/* Step 1 */}
      {step === 1 && (
        <form onSubmit={form1.handleSubmit(handleStep1)} className="bg-white border border-gray-200 rounded-lg p-6 space-y-5">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">支付方式</label>
            <div className="flex gap-3">
              {(['upi', 'pix'] as PaymentMethod[]).map((m) => (
                <label
                  key={m}
                  className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 border rounded-lg cursor-pointer transition-colors ${
                    paymentMethod === m
                      ? 'border-purple-500 bg-purple-50 text-purple-700'
                      : 'border-gray-200 text-gray-500 hover:border-gray-300'
                  }`}
                >
                  <input
                    type="radio"
                    value={m}
                    {...form1.register('payment_method')}
                    className="sr-only"
                  />
                  <span className="text-sm font-medium">{m.toUpperCase()}</span>
                </label>
              ))}
            </div>
            {form1.formState.errors.payment_method && (
              <p className="text-red-500 text-xs mt-1">{form1.formState.errors.payment_method.message}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">收款邮箱</label>
            <input
              type="email"
              {...form1.register('account_email')}
              placeholder="example@mail.com"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none"
            />
            {form1.formState.errors.account_email && (
              <p className="text-red-500 text-xs mt-1">{form1.formState.errors.account_email.message}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              加价金额 (CNY)
              <span className="text-gray-400 font-normal ml-1">可选</span>
            </label>
            <input
              type="number"
              step="0.01"
              {...form1.register('markup_cny')}
              placeholder="0"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none"
            />
          </div>

          {capacity && (
            <CapacityBar
              queuedCount={capacity.queued_count}
              availableSlots={capacity.available_slots}
              acceptingNew={capacity.accepting_new}
            />
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full py-2.5 text-sm font-medium text-white bg-purple-600 rounded-lg hover:bg-purple-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? '创建中...' : '下一步：提交 Checkout'}
          </button>
        </form>
      )}

      {/* Step 2 */}
      {step === 2 && (
        <form onSubmit={form2.handleSubmit(handleStep2)} className="bg-white border border-gray-200 rounded-lg p-6 space-y-5">
          <div className="text-sm text-gray-500 bg-gray-50 rounded-lg px-4 py-3">
            已创建任务 <span className="font-mono text-gray-700">#{taskId?.slice(0, 8)}</span>，
            请填写 Checkout 数据完成提交
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Access Token</label>
            <input
              type="text"
              {...form2.register('access_token')}
              placeholder="用于验证 Plus 状态"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none"
            />
            {form2.formState.errors.access_token && (
              <p className="text-red-500 text-xs mt-1">{form2.formState.errors.access_token.message}</p>
            )}
          </div>

          {paymentMethod === 'upi' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                Pay Link (Stripe UPI)
              </label>
              <input
                type="url"
                {...form2.register('pay_link')}
                placeholder="https://..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none"
              />
              <p className="text-xs text-gray-400 mt-1">Stripe UPI 支付链接，有效期约 5 分钟</p>
            </div>
          )}

          {paymentMethod === 'pix' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                BR Code (PIX)
              </label>
              <input
                type="text"
                {...form2.register('brcode')}
                placeholder="000201..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none"
              />
              <p className="text-xs text-gray-400 mt-1">PIX 码，有效期约 24 小时</p>
            </div>
          )}

          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => setStep(1)}
              className="flex-1 py-2.5 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
            >
              上一步
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="flex-1 py-2.5 text-sm font-medium text-white bg-purple-600 rounded-lg hover:bg-purple-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? '提交中...' : '提交 Checkout'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
