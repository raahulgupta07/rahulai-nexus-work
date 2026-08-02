<template>
    <div class="inline">
      <!-- Paperclip opens the OS file picker DIRECTLY (no intermediate modal).
           The hidden input lives outside the modal so it's always available;
           drag-drop onto the composer is handled by the parent (PromptBoxV2). -->
      <input
        type="file"
        ref="fileInput"
        @change="handleFilesUpload"
        class="hidden"
        multiple
      />
      <!-- Flag OFF: the paperclip behaves exactly as it always has — straight
           to the OS picker, no extra click. With the flag on the menu also
           renders on the landing composer (no report yet): the selection is
           carried into the first completion via createReport's query params. -->
      <button v-if="!localFolderAttachOn || localFoldersAccess === 'off'" @click="$refs.fileInput.click()"
       class="text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md p-1 flex items-center">
        <UIcon name="i-heroicons-paper-clip" />
        <span v-if="allFiles.length > 0" class="truncate max-w-[200px] text-xs ms-1 text-gray-500 dark:text-gray-400">
          <UTooltip :text="allFiles.map(file => file.filename).join(', ')">
          {{ allFiles.length }}
        </UTooltip>
        </span>
      </button>
      <!-- Flag ON: the same paperclip opens a small attach menu first. -->
      <!-- Direction-aware: prefer opening DOWNWARD (the landing composer sits
           mid-screen with free space below); Popper's flip flips it upward when
           there's no room below (the chat composer at the viewport bottom).
           Geometry decides — no per-page hardcoding, no clipping. -->
      <UPopover v-else :popper="{ placement: 'bottom-end' }" @update:open="onAttachMenuToggle">
        <button
         class="text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md p-1 flex items-center">
          <UIcon name="i-heroicons-paper-clip" />
          <span v-if="allFiles.length > 0" class="truncate max-w-[200px] text-xs ms-1 text-gray-500 dark:text-gray-400">
            <UTooltip :text="allFiles.map(file => file.filename).join(', ')">
            {{ allFiles.length }}
          </UTooltip>
          </span>
        </button>
        <template #panel="{ close }">
          <!-- Micro List: total height ≈290px so it fits above OR below the
               anchor on any screen; the row list is the only scrolling part. -->
          <div class="w-72 p-1 text-sm">
            <button
              class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800"
              @click="close(); $refs.fileInput.click()">
              <Icon name="heroicons-arrow-up-tray" class="w-4 h-4 text-gray-400" />
              <span>{{ $t('prompt.uploadFiles') }}</span>
            </button>

            <!-- Coming soon: the section stays visible so a member can see
                 folder sharing is planned, but nothing is listed or fetched. -->
            <div v-if="foldersComingSoon" class="mt-1 pt-1 border-t border-gray-100 dark:border-gray-800">
              <div class="px-2 py-1 flex items-center justify-between gap-2 min-w-0">
                <span class="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 whitespace-nowrap">
                  {{ $t('prompt.connectedFolders') }}
                </span>
                <span class="inline-flex items-center px-1.5 h-4 rounded bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400 text-[10px] font-medium flex-shrink-0">
                  {{ $t('profile.comingSoonBadge') }}
                </span>
              </div>
              <p class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">
                {{ $t('prompt.localFoldersComingSoon') }}
              </p>
            </div>

            <div v-else-if="foldersEnabled" class="mt-1 pt-1 border-t border-gray-100 dark:border-gray-800">
              <!-- Header: section label + the device this list belongs to, so a
                   stale/offline helper is obvious before the user clicks a row. -->
              <div class="px-2 py-1 flex items-center justify-between gap-2 min-w-0">
                <span class="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500 whitespace-nowrap flex-shrink-0">
                  {{ $t('prompt.connectedFolders') }}
                </span>
                <span v-if="foldersState?.paired" class="flex items-center gap-1 min-w-0">
                  <span
                    class="w-1.5 h-1.5 rounded-full flex-shrink-0"
                    :class="foldersState.online ? 'bg-green-500' : 'bg-gray-400 dark:bg-gray-600'"></span>
                  <span
                    class="truncate text-[10px] max-w-[120px]"
                    :class="foldersState.online ? 'text-green-600 dark:text-green-500' : 'text-gray-400 dark:text-gray-500'">
                    {{ shortDeviceName }}
                  </span>
                </span>
              </div>

              <div v-if="foldersLoading" class="px-2 py-2 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                <Spinner class="w-3 h-3 text-blue-500" />
                <span>{{ $t('prompt.localFoldersLoading') }}</span>
              </div>

              <p v-else-if="!foldersState?.paired" class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">
                {{ $t('prompt.localFoldersNotPaired') }}
              </p>

              <p v-else-if="!localFolders.length" class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">
                {{ $t('prompt.localFoldersNone') }}
              </p>

              <template v-else>
                <!-- Search matches folder name/path AND the file/table names
                     inside it — "where did I put sales.csv?" is the real question. -->
                <div class="px-2 pb-1">
                  <div class="flex items-center gap-1.5 rounded-md border border-gray-200 dark:border-gray-700 px-2 py-1 bg-white dark:bg-gray-900">
                    <Icon name="heroicons-magnifying-glass" class="w-3.5 h-3.5 flex-shrink-0 text-gray-400" />
                    <input
                      v-model="folderQuery"
                      type="text"
                      :placeholder="$t('prompt.searchFolders')"
                      class="flex-1 min-w-0 bg-transparent text-xs text-gray-700 dark:text-gray-200 placeholder-gray-400 focus:outline-none" />
                  </div>
                </div>

                <p v-if="!displayFolders.length" class="px-2 py-2 text-xs text-gray-400 dark:text-gray-500">
                  {{ $t('prompt.noFolderMatches') }}
                </p>

                <!-- Micro rows: ONE line each (name + compact meta right, full
                     path as the row's tooltip). ALL folders live here — no
                     "Show all"; the list itself scrolls past ~4 rows. -->
                <div v-else class="max-h-40 overflow-y-auto">
                  <button
                    v-for="folder in displayFolders"
                    :key="folder.name"
                    class="group w-full flex items-center gap-2 px-2 py-1 rounded-md text-left hover:bg-gray-50 dark:hover:bg-gray-800"
                    :class="{ 'opacity-50': removingFolder === folder.name }"
                    :title="folder.path || folder.name"
                    @click="onFolderClick(folder)">
                    <Icon name="heroicons-folder" class="w-4 h-4 flex-shrink-0"
                          :class="folderHasContent(folder) ? 'text-green-600 dark:text-green-500' : 'text-gray-400'" />
                    <span
                      class="truncate font-medium flex-1 min-w-0"
                      :class="folderHasContent(folder) ? 'text-gray-700 dark:text-gray-200' : 'text-gray-400 dark:text-gray-500'">
                      {{ folder.name }}
                    </span>
                    <span
                      v-if="!folderHasContent(folder)"
                      class="flex-shrink-0 rounded px-1 text-[10px] bg-amber-50 text-amber-600 dark:bg-amber-950 dark:text-amber-500">
                      {{ $t('prompt.noDataFiles') }}
                    </span>
                    <span v-else class="flex-shrink-0 text-[10px] text-gray-400 dark:text-gray-500">
                      {{ folderMeta(folder) }}
                    </span>
                    <Icon v-if="isAttached(folder.name)" name="heroicons-check" class="w-4 h-4 flex-shrink-0 text-blue-500" />
                    <span
                      class="hidden group-hover:inline-flex items-center flex-shrink-0 text-gray-400 hover:text-red-500 cursor-pointer"
                      :title="$t('prompt.stopSharingFolder')"
                      @click.stop="removeSharedFolder(folder.name)">
                      <Spinner v-if="removingFolder === folder.name" class="w-3.5 h-3.5" />
                      <Icon v-else name="heroicons-x-mark" class="w-3.5 h-3.5" />
                    </span>
                  </button>
                </div>

                <p v-if="foldersState && !foldersState.online" class="px-2 pt-1 pb-2 text-[11px] text-amber-600 dark:text-amber-500">
                  {{ $t('prompt.localFoldersOffline') }}
                </p>
              </template>

              <!-- "Add folder" from the browser: the server only queues the
                   request; the user's own helper validates the path, whitelists,
                   scans (schema only) and publishes. Shown only when a device is
                   paired AND online (the helper must be there to answer). -->
              <template v-if="foldersState?.paired && foldersState?.online">
                <!-- Primary: pop the NATIVE folder chooser on the user's own
                     device (helper opens it) — click a folder, done, no typing. -->
                <button
                  class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left font-medium text-green-600 dark:text-green-500 hover:bg-green-50 dark:hover:bg-gray-800 disabled:opacity-60"
                  :disabled="addFolderBusy"
                  @click="submitAddFolder(true)">
                  <Spinner v-if="addFolderBusy && addFolderPicking" class="w-4 h-4 flex-shrink-0 text-green-600" />
                  <Icon v-else name="heroicons-folder-plus" class="w-4 h-4 flex-shrink-0" />
                  <span>{{ addFolderBusy && addFolderPicking ? $t('prompt.pickLocalFolderWaiting') : $t('prompt.connectFolderCta') }}</span>
                </button>
                <!-- Fallback: type a path (remote/SSH setups). -->
                <button
                  v-if="!addFolderOpen"
                  class="w-full px-2 py-0.5 text-left text-[11px] text-gray-400 dark:text-gray-500 hover:text-gray-600"
                  @click="addFolderOpen = true">
                  {{ $t('prompt.addLocalFolder') }}
                </button>
                <div v-else class="px-2 py-1.5">
                  <div class="flex items-center gap-1.5">
                    <input
                      v-model="addFolderPath"
                      :placeholder="$t('prompt.addLocalFolderPlaceholder')"
                      class="flex-1 min-w-0 text-xs font-mono border border-gray-200 dark:border-gray-700 rounded-md px-2 py-1 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200"
                      :disabled="addFolderBusy"
                      @keydown.enter.prevent="submitAddFolder(false)" />
                    <button
                      class="text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-md px-2 py-1 disabled:opacity-50"
                      :disabled="addFolderBusy || !addFolderPath.trim()"
                      @click="submitAddFolder(false)">
                      <Spinner v-if="addFolderBusy && !addFolderPicking" class="w-3 h-3 text-white" />
                      <span v-else>{{ $t('prompt.addLocalFolderGo') }}</span>
                    </button>
                  </div>
                  <p v-if="!addFolderBusy" class="pt-1 text-[11px] text-gray-400 dark:text-gray-500">{{ $t('prompt.addLocalFolderHint') }}</p>
                  <p v-else class="pt-1 text-[11px] text-gray-400 dark:text-gray-500">{{ $t('prompt.addLocalFolderWaiting') }}</p>
                </div>
                <p v-if="addFolderError" class="px-2 pt-1 pb-1 text-[11px] text-red-600 dark:text-red-400">{{ addFolderError }}</p>
              </template>

              <!-- The whole point of local runtime: say it where the user is
                   deciding whether to attach a folder at all. -->
<!-- privacy note lives in the native picker dialog + connect toast now,
                   not as permanent menu height. -->
            </div>
          </div>
        </template>
      </UPopover>
      <UModal v-model="isFilesOpen">
        <div class="p-4 min-h-72 flex flex-col">
          <h2 class="text-md font-semibold pb-2">Upload files</h2>
          <hr />

          <span class="text-sm text-gray-500 dark:text-gray-400 mt-4 mb-2 block">Upload excel or PDF files to analyze</span>
          <div 
            v-if="allFiles.length === 0"
            @dragover.prevent="isDragging = true"
            @dragenter.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
            :class="['drop-zone cursor-pointer', isDragging ? 'drop-zone-active' : '']"
            @click="$refs.fileInput.click()"
          > 
            <div class="flex mt-2 flex-col items-center justify-center py-10">
              <Icon 
                name="heroicons-cloud-arrow-up" 
                :class="['w-12 h-12 transition-colors', isDragging ? 'text-blue-500' : 'text-blue-400']" 
              />
              <span class="mt-3 text-sm text-blue-500">
                {{ isDragging ? 'Drop files here' : 'Click or drag files to upload' }}
              </span>
            </div>
          </div>
          <ul
            v-if="allFiles.length > 0"
            class="w-full mt-4 max-h-64 overflow-y-auto"
            @dragover.prevent="isDragging = true"
            @dragenter.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop">
            <li 
              v-for="(file, index) in allFiles" 
              :key="file.id" 
              class="text-xs py-2 text-gray-600 dark:text-gray-400 flex items-center justify-between border-b border-gray-100 dark:border-gray-800 last:border-b-0">
              <div class="flex items-center gap-1.5 min-w-0 flex-1">
                <!-- Determinate ring while bytes move, spinner once the server
                     takes over: the shape itself says whether there is a
                     number behind it. -->
                <svg v-if="file.status === 'processing' && file.upload_stage === 'uploading' && file.upload_total" class="w-3.5 h-3.5 flex-shrink-0 -rotate-90" viewBox="0 0 20 20">
                  <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="3" class="text-gray-200 dark:text-gray-700" />
                  <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" class="text-blue-500"
                          :stroke-dasharray="50.27" :stroke-dashoffset="50.27 * (1 - (file.upload_percent || 0) / 100)" />
                </svg>
                <Spinner v-else-if="file.status === 'processing'" class="w-3 h-3 text-blue-500 flex-shrink-0" />
                <Icon v-else-if="file.status === 'uploaded'" name="heroicons-check-circle" class="text-blue-500 w-4 h-4 flex-shrink-0" />
                <Icon v-else-if="file.status === 'error'" name="heroicons-x-circle" class="text-red-500 w-4 h-4 flex-shrink-0" />

                <span class="truncate">{{ file.filename }}</span>
                <!-- Bytes sent, while they are being sent. Once the last byte
                     is up the number is replaced by the name of what the server
                     is doing — a bar parked at 100% reads as a hang. -->
                <span v-if="file.status === 'processing' && file.upload_stage === 'uploading' && file.upload_total" class="flex-shrink-0 font-mono text-[10px] text-gray-400 dark:text-gray-500 tabular-nums">
                  {{ file.upload_percent }}% · {{ formatBytes(file.upload_loaded) }}/{{ formatBytes(file.upload_total) }}
                </span>
                <span v-else-if="file.status === 'processing'" class="flex-shrink-0 text-[10px] text-gray-400 dark:text-gray-500">
                  {{ $t('files.uploadProcessing') }}
                </span>
              </div>
              <div class="flex-shrink-0 ps-2">
              <button @click="removeFile(file)" class="text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-full ms-auto items-center justify-center">
                <Icon name="heroicons-x-mark" class="w-4 h-4" />
              </button>
            </div>
            </li>
          </ul>
          <div v-if="projectFiles.length > 0" class="w-full mt-3">
            <div class="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-1">
              {{ $t('projects.overview.fromProject', { name: projectName }) }}
            </div>
            <ul class="w-full max-h-32 overflow-y-auto">
              <li
                v-for="file in projectFiles"
                :key="file.id"
                class="text-xs py-1.5 text-gray-500 dark:text-gray-400 flex items-center gap-1.5 border-b border-gray-100 dark:border-gray-800 last:border-b-0">
                <Icon name="heroicons-folder" class="w-3.5 h-3.5 flex-shrink-0 text-gray-400" />
                <span class="truncate">{{ file.filename }}</span>
              </li>
            </ul>
          </div>
          <div
            :class="['text-center items-center py-4 mt-3 rounded-lg transition-all cursor-pointer',
              isDragging ? 'bg-blue-50 dark:bg-blue-950 border-1 border-dashed border-blue-400' : 'border-2 border-dashed border-gray-200 dark:border-gray-700 hover:border-blue-300 hover:bg-blue-50/50 dark:hover:bg-blue-950']"
            v-if="allFiles.length > 0"
            @dragover.prevent="isDragging = true"
            @dragenter.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
            @click="$refs.fileInput.click()">
            <div class="text-sm text-blue-500 flex items-center justify-center gap-2 w-full">
              <Icon name="heroicons-cloud-arrow-up" class="w-5 h-5" />
              {{ isDragging ? 'Drop files here' : 'Click or drag to upload more' }}
            </div>
          </div>
        </div>
      </UModal>
    </div>
  </template>
  
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import Spinner from './Spinner.vue';
// Uploads go through XHR so the chip can show real progress; see the composable
// for why fetch cannot.
const { upload: uploader, formatBytes } = useUploadWithProgress();

  
const isFilesOpen = ref(false);
const allFiles = ref([]);
const isDragging = ref(false);

  const props = defineProps({
    report_id: String,
    // Project (folder) of the report — its files are inherited live into the
    // agent context, so the modal lists them read-only for visibility.
    project: { type: Object, default: null },
  })

  const report_id = props.report_id;

  const projectFiles = ref<any[]>([]);
  const projectName = computed(() => (props.project as any)?.name || '');
  watch(isFilesOpen, async (open) => {
    if (!open || !(props.project as any)?.id) { if (!open) return; projectFiles.value = []; return }
    try {
      const resp = await useMyFetch(`/projects/${(props.project as any).id}`, { method: 'GET' });
      const proj = (resp as any).data?.value;
      projectFiles.value = Array.isArray(proj?.files) ? proj.files : [];
    } catch { projectFiles.value = [] }
  });

  const emit = defineEmits(['update:uploadedFiles', 'update:localFolders']);

  // Gates the whole "Attach local folder" branch. Off => the paperclip keeps
  // opening the OS picker directly, exactly as before.
  const { localFolderAttachOn } = useAppSettings();
  // Two independent gates: the build flag says the feature exists at all, the
  // org setting says whether this organisation has released it to members.
  const { localFoldersAccess } = useOrgSettings();
  const foldersEnabled = computed(() => localFoldersAccess.value === 'on');
  const foldersComingSoon = computed(() => localFoldersAccess.value === 'coming_soon');

  async function getReportFiles() {
    if (report_id) {
      const { data } = await useMyFetch(`/reports/${report_id}/files`, {
        method: 'GET',
      });
      // Filter out files that have been used in a completion (completion_id is set)
      // This allows newly uploaded images to show, but hides them after they're submitted.
      // Also hide files inherited from a data source — those belong to the agent,
      // not to this chat turn (the agent flows them through report.files into
      // FilesContextBuilder regardless).
      const unusedFiles = data.value.filter(file => !file.completion_id && !file.from_data_source);
      allFiles.value = unusedFiles.map(file => ({ ...file, status: 'uploaded' }));
    }
  }

  function generateUniqueId() {
    return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  }

  function handleFilesUpload(e) {
    const selectedFiles = Array.from(e.target.files).map(file => ({
      id: generateUniqueId(),
      file,
      filename: file.name,
      status: "processing"
    }));
    allFiles.value.push(...selectedFiles);
    // Emit immediately so parent can show processing state
    emit('update:uploadedFiles', [...allFiles.value]);
    selectedFiles.forEach(file => uploadFile(file));
  }

  function handleDrop(e) {
    isDragging.value = false;
    const droppedFiles = Array.from(e.dataTransfer.files).map(file => ({
      id: generateUniqueId(),
      file,
      filename: file.name,
      status: "processing"
    }));
    allFiles.value.push(...droppedFiles);
    // Emit immediately so parent can show processing state
    emit('update:uploadedFiles', [...allFiles.value]);
    droppedFiles.forEach(file => uploadFile(file));
  }
  
  async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file.file); // Use the actual File object

    // Add report_id to formData if it exists
    if (report_id) {
      formData.append('report_id', report_id);
    }

    // Update file status to 'processing' before the upload starts
    const index = allFiles.value.findIndex(f => f.id === file.id);
    if (index !== -1) {
      allFiles.value[index] = { ...allFiles.value[index], status: 'processing' };
    }

    try {
      // ★ XHR, not useMyFetch: the fetch API emits no upload progress, so this
      // chip could only ever spin. `percent` is bytes sent; once they are all
      // sent the server still parses (and may split a workbook per sheet, and
      // may re-learn the agent), which is why the chip switches to a named
      // 'processing' stage instead of leaving a full bar sitting at 100%.
      const handle = uploader('/files', formData);
      const patch = () => {
        const i = allFiles.value.findIndex(f => f.id === file.id);
        if (i === -1) return;
        allFiles.value[i] = {
          ...allFiles.value[i],
          status: 'processing',
          upload_stage: handle.stage.value,
          upload_percent: handle.percent.value,
          upload_loaded: handle.loaded.value,
          upload_total: handle.total.value,
        };
        emit('update:uploadedFiles', [...allFiles.value]);
      };
      const stopPct = watch(handle.percent, patch);
      const stopStage = watch(handle.stage, patch);
      const { data: raw, error: err } = await handle.promise;
      stopPct(); stopStage();
      const data = { value: raw };
      const error = { value: err };

      // Check for errors in the response
      if (error.value || !data.value) {
        console.error('Error uploading file:', error.value);
        const idx = allFiles.value.findIndex(f => f.id === file.id);
        if (idx !== -1) {
          allFiles.value[idx] = { ...allFiles.value[idx], status: 'error' };
        }
        return;
      }

      // Update the file status after successful upload
      const successIdx = allFiles.value.findIndex(f => f.id === file.id);
      if (successIdx !== -1) {
        allFiles.value[successIdx] = { ...data.value, status: 'uploaded' };
      }

      // Emit the updated list of files to the parent component
      emit('update:uploadedFiles', [...allFiles.value]);
    } catch (error) {
      console.error('Error uploading file:', error);
      // Update file status to 'error'
      const idx = allFiles.value.findIndex(f => f.id === file.id);
      if (idx !== -1) {
        allFiles.value[idx] = { ...allFiles.value[idx], status: 'error' };
      }
      // Emit updated list with error status
      emit('update:uploadedFiles', [...allFiles.value]);
    }
  }

  async function removeFile(file) {
    // Remove the file from allFiles array
    allFiles.value = allFiles.value.filter(f => f !== file);

    // If the file has an ID and report_id exists, delete it from the server
    if (file.id && report_id) {
      try {
        await useMyFetch(`/reports/${report_id}/files/${file.id}`, {
          method: 'DELETE',
        });
      } catch (error) {
        console.error('Error deleting file from server:', error);
        // Optionally, you can handle the error (e.g., show a notification to the user)
      }
    }

    // Emit the updated list of files
    emit('update:uploadedFiles', [...allFiles.value]);
  }

  onMounted(async () => {
    await getReportFiles();
    // Emit existing files so parent knows about them
    emit('update:uploadedFiles', [...allFiles.value]);
  });

  // Programmatically upload files (for drag & drop from parent)
  function uploadFilesFromParent(files: FileList | File[]) {
    const fileArray = Array.from(files).map(file => ({
      id: generateUniqueId(),
      file,
      filename: file.name,
      status: "processing" as const
    }));
    allFiles.value.push(...fileArray);
    // Emit immediately so parent can show processing state
    emit('update:uploadedFiles', [...allFiles.value]);
    fileArray.forEach(file => uploadFile(file));
  }

  // Clear image files from local state (no API call - backend handles deletion)
  function clearImages() {
    allFiles.value = allFiles.value.filter(f => {
      const contentType = f.content_type || f.type || ''
      return !contentType.startsWith('image/')
    })
    emit('update:uploadedFiles', [...allFiles.value])
  }

  // ---------------------------------------------------------------------- //
  //  Local folders (flag HYBRID_LOCAL_FOLDER_ATTACH)
  //  Folders the user's paired helper shares. We only ever hold their SCHEMA —
  //  the files themselves are queried on the user's machine and never upload.
  // ---------------------------------------------------------------------- //
  const localFolders = ref<any[]>([]);
  const foldersState = ref<any>(null);
  const foldersLoading = ref(false);
  const attachedFolders = ref<string[]>([]);

  async function loadLocalFolders() {
    // Never ask when the org hasn't released folders — the endpoint 403s.
    if (!localFolderAttachOn.value || !foldersEnabled.value || foldersLoading.value) return;
    foldersLoading.value = true;
    try {
      const { data } = await useMyFetch<any>('/api/local-runtime/folders', { method: 'GET' } as any);
      const body: any = data.value || {};
      foldersState.value = body;
      localFolders.value = body.folders || [];
      // A folder that stopped being shared can't stay attached — drop it so the
      // next message doesn't claim data we can no longer reach.
      const known = new Set(localFolders.value.map((f: any) => f.name));
      const kept = attachedFolders.value.filter(n => known.has(n));
      if (kept.length !== attachedFolders.value.length) {
        attachedFolders.value = kept;
        emit('update:localFolders', folderSelection());
      }
    } catch {
      foldersState.value = { paired: false, online: false };
      localFolders.value = [];
    } finally {
      foldersLoading.value = false;
    }
  }

  function onAttachMenuToggle(open: boolean) {
    if (open) loadLocalFolders();
  }

  // Menu state: search + "show all". The API list is append-ordered, so the
  // newest folder a user connected is the LAST one — reverse to lead with it.
  const folderQuery = ref('');

  const orderedFolders = computed<any[]>(() => [...localFolders.value].reverse());

  // "Rahuls-MacBook-Pro.local" → "Rahuls-MacBook-Pro": the mDNS suffix wastes
  // header space and reads like a bug to non-technical users.
  const shortDeviceName = computed(() =>
    String(foldersState.value?.device_name || '').replace(/\.local$/i, ''));

  const filteredFolders = computed<any[]>(() => {
    const q = folderQuery.value.trim().toLowerCase();
    if (!q) return orderedFolders.value;
    return orderedFolders.value.filter((f: any) => {
      const tables = Array.isArray(f?.tables) ? f.tables : [];
      const hay = [f?.name, f?.path, ...tables.flatMap((x: any) => [x?.name, x?.file])];
      return hay.some((s: any) => String(s || '').toLowerCase().includes(q));
    });
  });

  // Micro List: every (filtered) folder renders; the list itself scrolls.
  const displayFolders = computed<any[]>(() => filteredFolders.value);

  function fmtRows(n: number) {
    const v = Number(n) || 0;
    return v >= 1000 ? `${Math.round(v / 100) / 10}k` : String(v);
  }

  function folderDocCount(folder: any) {
    const docs = Array.isArray(folder?.documents) ? folder.documents : [];
    return docs.length || Number(folder?.doc_count) || 0;
  }

  function folderHasContent(folder: any) {
    return !!(Number(folder?.table_count) || folderDocCount(folder));
  }

  function folderMeta(folder: any) {
    const tables = Array.isArray(folder?.tables) ? folder.tables : [];
    const count = tables.length || Number(folder?.table_count) || 0;
    const parts: string[] = [];
    if (count > 0) {
      parts.push(count === 1 ? t('prompt.folderFileCountOne') : t('prompt.folderFileCount', { count }));
      const formats = Array.from(new Set(
        tables.map((x: any) => String(x?.format || '').toUpperCase()).filter(Boolean)));
      if (formats.length) parts.push(formats.join('/'));
      const rows = tables.reduce((sum: number, x: any) => sum + (Number(x?.row_count) || 0), 0);
      if (rows > 0) parts.push(t('prompt.folderRows', { count: fmtRows(rows) }));
    }
    const docs = folderDocCount(folder);
    if (docs > 0) parts.push(docs === 1 ? t('prompt.folderDocCountOne') : t('prompt.folderDocCount', { count: docs }));
    return parts.join(' · ');
  }

  // A folder the helper found no data files in has nothing to attach — clicking
  // it would put a chip on the message that promises data we can't query.
  function onFolderClick(folder: any) {
    if (!folderHasContent(folder)) return;
    toggleFolder(folder);
  }

  // "Add folder" from the browser — queue the request, then poll the folder
  // list until the helper has validated + scanned it (or report its error).
  const { t } = useI18n();
  const addFolderOpen = ref(false);
  const addFolderPath = ref('');
  const addFolderBusy = ref(false);
  const addFolderPicking = ref(false);
  const addFolderError = ref('');

  const removingFolder = ref('');

  async function removeSharedFolder(name: string) {
    if (removingFolder.value) return;
    removingFolder.value = name;
    addFolderError.value = '';
    try {
      const { data, error } = await useMyFetch<any>(
        `/api/local-runtime/folders/${encodeURIComponent(name)}`, { method: 'DELETE' } as any);
      if (error.value || !data.value?.queued) {
        addFolderError.value = (error.value as any)?.data?.detail || t('prompt.addLocalFolderFailed');
        return;
      }
      const jobId = data.value.job_id;
      for (let i = 0; i < 15; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const { data: st } = await useMyFetch<any>(`/api/local-runtime/folders/request/${jobId}`, { method: 'GET' } as any);
        const s = st.value?.status;
        if (s === 'done') {
          detachFolder(name);
          await loadLocalFolders();
          return;
        }
        if (s === 'error' || s === 'expired') {
          addFolderError.value = st.value?.error || t('prompt.addLocalFolderFailed');
          return;
        }
      }
      addFolderError.value = t('prompt.addLocalFolderTimeout');
    } catch {
      addFolderError.value = t('prompt.addLocalFolderFailed');
    } finally {
      removingFolder.value = '';
    }
  }

  async function submitAddFolder(pick: boolean) {
    const path = addFolderPath.value.trim();
    if (addFolderBusy.value || (!pick && !path)) return;
    addFolderBusy.value = true;
    addFolderPicking.value = pick;
    addFolderError.value = '';
    try {
      const { data, error } = await useMyFetch<any>('/api/local-runtime/folders/request', {
        method: 'POST',
        body: pick ? { pick: true } : { path },
      } as any);
      if (error.value || !data.value?.queued) {
        addFolderError.value = (error.value as any)?.data?.detail
          || t('prompt.addLocalFolderFailed');
        return;
      }
      // Poll the JOB itself: done → refresh list; error → show why. The pick
      // flow waits on a human choosing in the native dialog, so give it time.
      const jobId = data.value.job_id;
      const maxTries = pick ? 120 : 15;   // ~4 min while the dialog is open / ~30 s typed
      for (let i = 0; i < maxTries; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const { data: st } = await useMyFetch<any>(`/api/local-runtime/folders/request/${jobId}`, { method: 'GET' } as any);
        const s = st.value?.status;
        if (s === 'done') {
          await loadLocalFolders();
          addFolderOpen.value = false;
          addFolderPath.value = '';
          return;
        }
        if (s === 'error' || s === 'expired') {
          const err = st.value?.error || '';
          addFolderError.value = err === 'cancelled' ? '' : (err || t('prompt.addLocalFolderFailed'));
          return;
        }
      }
      addFolderError.value = t('prompt.addLocalFolderTimeout');
    } catch {
      addFolderError.value = t('prompt.addLocalFolderFailed');
    } finally {
      addFolderBusy.value = false;
      addFolderPicking.value = false;
    }
  }

  function isAttached(name: string) {
    return attachedFolders.value.includes(name);
  }

  // The parent renders chips from this, so it carries the display data too.
  function folderSelection() {
    return attachedFolders.value.map(name => {
      const f = localFolders.value.find((x: any) => x.name === name);
      return {
        name,
        table_count: f?.table_count ?? 0,
        online: !!foldersState.value?.online,
      };
    });
  }

  function toggleFolder(folder: any) {
    attachedFolders.value = isAttached(folder.name)
      ? attachedFolders.value.filter(n => n !== folder.name)
      : [...attachedFolders.value, folder.name];
    emit('update:localFolders', folderSelection());
  }

  function detachFolder(name: string) {
    if (!isAttached(name)) return;
    attachedFolders.value = attachedFolders.value.filter(n => n !== name);
    emit('update:localFolders', folderSelection());
  }

  // Expose methods for parent component
  defineExpose({
    refresh: async () => {
      await getReportFiles();
      emit('update:uploadedFiles', [...allFiles.value]);
    },
    // Local folder attachment (no-ops when the flag is off)
    detachFolder,
    getAttachedFolderNames: () => [...attachedFolders.value],
    // Non-image files currently attached to the composer (report-scoped) — the
    // sender stamps these names onto each message so bubbles can show them.
    getAttachedFileNames: () => (allFiles.value || [])
      .filter((f: any) => !(f?.content_type || '').startsWith('image/'))
      .map((f: any) => f?.filename)
      .filter((n: any) => typeof n === 'string' && n),
    // Re-hydration on report (re)open: adopt the conversation's sticky folders,
    // then refresh the live list so chips gain real table counts + online state.
    setAttachedFolders: (names: string[]) => {
      attachedFolders.value = (names || []).filter(n => typeof n === 'string' && n);
      loadLocalFolders().then(() => emit('update:localFolders', folderSelection()));
    },
    refreshLocalFolders: loadLocalFolders,
    // Upload files programmatically (for drag & drop on prompt area)
    uploadFiles: uploadFilesFromParent,
    // Remove a file (for inline display remove button)
    removeFile,
    // Clear images from local state (called on submit, backend deletes them)
    clearImages,
    // Open the file modal
    open: () => { isFilesOpen.value = true; }
  });

  </script>
  
<style scoped>
.drop-zone {
  border: 1px dashed #e5e7eb;
  border-radius: 12px;
  transition: all 0.2s ease;
  margin-top: 0.75rem;
}

.drop-zone:hover {
  border-color: #93c5fd;
  background-color: #f0f9ff;
}

.drop-zone-active {
  border-color: #3b82f6;
  background-color: #eff6ff;
}
</style>