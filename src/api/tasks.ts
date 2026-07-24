import { apiClient } from './client';
import type {
  Task,
  CapacityInfo,
  CreateTaskRequest,
  SubmitCheckoutRequest,
  RefreshCheckoutRequest,
} from '../types';

interface ApiTask {
  task_id: string;
  payment_method: string;
  account_email: string;
  status: string;
  markup_cny: number;
  publisher_price_cny?: number;
  created_at: string;
  updated_at: string;
  checkout_data?: Task['checkout_data'];
}

function mapTask(raw: ApiTask): Task {
  return {
    id: raw.task_id,
    payment_method: raw.payment_method as Task['payment_method'],
    account_email: raw.account_email,
    status: raw.status as Task['status'],
    markup_cny: raw.markup_cny,
    total_charge_cny: raw.publisher_price_cny ?? 0,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
    checkout_data: raw.checkout_data,
  };
}

export async function fetchTasks(): Promise<Task[]> {
  const data = await apiClient<{ tasks: ApiTask[] }>('/tasks');
  return data.tasks.map(mapTask);
}

export async function fetchTask(id: string): Promise<Task> {
  const data = await apiClient<ApiTask>(`/tasks/${id}`);
  return mapTask(data);
}

export async function createTask(data: CreateTaskRequest): Promise<Task> {
  const res = await apiClient<ApiTask>('/tasks', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  return mapTask(res);
}

export async function submitCheckout(
  taskId: string,
  data: SubmitCheckoutRequest,
): Promise<Task> {
  const res = await apiClient<ApiTask>(`/tasks/${taskId}/submit-checkout`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
  return mapTask(res);
}

export async function refreshCheckout(
  taskId: string,
  data: RefreshCheckoutRequest,
): Promise<Task> {
  const res = await apiClient<ApiTask>(`/tasks/${taskId}/refresh-checkout`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
  return mapTask(res);
}

export async function cancelTask(taskId: string): Promise<Task> {
  const res = await apiClient<ApiTask>(`/tasks/${taskId}/cancel`, {
    method: 'POST',
  });
  return mapTask(res);
}

export function fetchQrStatus(taskId: string): Promise<{ status: string }> {
  return apiClient<{ status: string }>(`/tasks/${taskId}/qr-status`);
}

export function fetchCapacity(
  paymentMethod: string,
): Promise<CapacityInfo> {
  return apiClient<CapacityInfo>(
    `/capacity?payment_method=${paymentMethod}`,
  );
}
