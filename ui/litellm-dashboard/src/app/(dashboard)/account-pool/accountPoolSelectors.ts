/** 本文件提供号池环境查询、筛选、排序与分页的纯选择器。 */

import type { AccountPoolEnvironment, AccountPoolStatus } from "./AccountPoolTypes";

export const sortAccountPoolEnvironments = (
  environments: readonly AccountPoolEnvironment[],
): AccountPoolEnvironment[] =>
  [...environments].sort((left, right) => {
    const updatedDifference = Date.parse(right.updated_at) - Date.parse(left.updated_at);
    if (Number.isFinite(updatedDifference) && updatedDifference !== 0) return updatedDifference;
    const nameDifference = left.name.localeCompare(right.name, "zh-CN");
    return nameDifference !== 0 ? nameDifference : left.id.localeCompare(right.id);
  });

export const filterAccountPoolEnvironments = (
  environments: readonly AccountPoolEnvironment[],
  search: string,
  status: "all" | AccountPoolStatus,
): AccountPoolEnvironment[] => {
  const normalizedSearch = search.trim().toLocaleLowerCase();
  return sortAccountPoolEnvironments(
    environments.filter((environment) => {
      const matchesStatus = status === "all" || environment.status === status;
      const matchesSearch =
        normalizedSearch.length === 0 ||
        environment.name.toLocaleLowerCase().includes(normalizedSearch) ||
        environment.id.toLocaleLowerCase().includes(normalizedSearch);
      return matchesStatus && matchesSearch;
    }),
  );
};

export const paginateAccountPoolEnvironments = (
  environments: readonly AccountPoolEnvironment[],
  page: number,
  pageSize: number,
): AccountPoolEnvironment[] => {
  const pageCount = Math.max(1, Math.ceil(environments.length / pageSize));
  const currentPage = Math.min(Math.max(1, page), pageCount);
  return environments.slice((currentPage - 1) * pageSize, currentPage * pageSize);
};
