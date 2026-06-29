import { createRouter, createWebHistory } from 'vue-router'
import UploadView from '../views/UploadView.vue'
import JobListView from '../views/JobListView.vue'
import SpecView from '../views/SpecView.vue'

const routes = [
  { path: '/',       name: 'upload',  component: UploadView },
  { path: '/jobs',   name: 'jobs',    component: JobListView },
  { path: '/spec',   name: 'spec',    component: SpecView },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
