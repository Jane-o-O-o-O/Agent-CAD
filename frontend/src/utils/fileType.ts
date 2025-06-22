import type { Component } from 'vue';
import FileIcon from '../components/icons/FileIcon.vue';
import CodeFileIcon from '../components/icons/CodeFileIcon.vue';
import UnknownFilePreview from '../components/filePreviews/UnknownFilePreview.vue';
import MarkdownFilePreview from '../components/filePreviews/MarkdownFilePreview.vue';
import CodeFilePreview from '../components/filePreviews/CodeFilePreview.vue';
import ImageFilePreview from '../components/filePreviews/ImageFilePreview.vue';

export interface FileType {
  icon: Component;
  preview: Component;
}

const codeFileExtensions = [
  'py', 'js', 'ts', 'jsx', 'tsx', 'vue',
  'java', 'c', 'cpp', 'h', 'hpp',
  'go', 'rust', 'php', 'ruby', 'swift',
  'kotlin', 'scala', 'haskell', 'erlang', 'elixir',
  'ocaml', 'fsharp', 'dart', 'julia',
  'lua', 'perl', 'r', 'sh', 'bash',
  'css', 'scss', 'sass', 'less', 'txt',
  'html', 'xml', 'json', 'yaml', 'yml',
  'sql', 'dockerfile', 'toml', 'ini', 'conf',
];

const imageFileExtensions = [
  'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff', 'tif', 'heic', 'heif',
];

export const getFileType = (filename: string): FileType => {
  const file_extension = filename.split('.').pop()?.toLowerCase();
  
  if (file_extension === 'md') {
    return {
      icon: FileIcon,
      preview: MarkdownFilePreview,
    };
  }
  
  if (file_extension && codeFileExtensions.includes(file_extension)) {
    return {
      icon: CodeFileIcon,
      preview: CodeFilePreview,
    };
  }

  if (file_extension && imageFileExtensions.includes(file_extension)) {
    return {
      icon: FileIcon,
      preview: ImageFilePreview,
    };
  }
  
  return {
    icon: FileIcon,
    preview: UnknownFilePreview,
  };
}; 