import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const uploadPsd = (formData) =>
  api.post('/banner/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

export const getJob = (id) => api.get(`/banner/job/${id}`)

export const listJobs = () => api.get('/banner/jobs')

export const downloadZip = (id) =>
  api.get(`/banner/job/${id}/download`, { responseType: 'blob' })

export const downloadImage = (jobId, fileName) =>
  api.get(`/banner/job/${jobId}/image/${encodeURIComponent(fileName)}`, { responseType: 'blob' })

export const previewUrl = (jobId, fileName) =>
  `/api/banner/job/${jobId}/preview/${encodeURIComponent(fileName)}`

export const listSpecs = (media) =>
  api.get('/spec', { params: media ? { media } : {} })

export const saveSpec = (spec) => api.post('/spec', spec)

export const deleteSpec = (id) => api.delete(`/spec/${id}`)

export const initSpecs = (reset = false) =>
  api.post('/spec/init', null, { params: { reset } })

export const analyzeBanner = (formData) =>
  api.post('/banner/analyze', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 90000,
  })

export const compareJob = (jobId, specId) =>
  api.post(`/banner/jobs/${jobId}/compare`, { specId }, { timeout: 60000 })

export const applyCompare = (jobId, compareId, specId, candidate) =>
  api.post(`/banner/jobs/${jobId}/compare/${compareId}/apply`, { specId, candidate })

export const compareFileUrl = (compareId, fileName) =>
  `/api/banner/compare/${compareId}/files/${encodeURIComponent(fileName)}`

export const analyzePsdLayers = (formData) =>
  api.post('/banner/analyze-psd', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
