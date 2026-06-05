import {
  DraftingCompass,
  TerminalSquare,
  Workflow,
  type LucideIcon,
} from 'lucide-vue-next';

export interface CadPrompt {
  title: string;
  shortTitle: string;
  caption: string;
  message: string;
  icon: LucideIcon;
}

export const cadPrompts: CadPrompt[] = [
  {
    title: '完成 CAD 绘图',
    shortTitle: '图纸绘制',
    caption: '理解要求，生成图纸、脚本或模型文件',
    message: '帮我完成这个 CAD 绘图任务。请先理解图纸要求，再给出可执行的绘图步骤，并尽量生成可直接打开或运行的 CAD 文件/脚本。',
    icon: DraftingCompass,
  },
  {
    title: '拆解建模方案',
    shortTitle: '建模方案',
    caption: '任务拆分、结构规划、建模步骤',
    message: '帮我把这个 CAD 任务拆成可执行方案，并给出建模步骤。',
    icon: Workflow,
  },
  {
    title: '检查图纸问题',
    shortTitle: '图纸检查',
    caption: '尺寸、结构、标注、图层与可制造性',
    message: '帮我检查这个 CAD 图纸或建模结果，指出尺寸、结构、标注、图层和可制造性方面的问题，并给出具体修改建议。',
    icon: TerminalSquare,
  },
  {
    title: '文档转 DXF',
    shortTitle: '文档转图',
    caption: '从上传文档/图片提取要求并生成 DXF',
    message: '请读取我上传的文档或图片，提取其中的 CAD 绘图要求，先确认关键尺寸和不确定项，然后生成可打开的 DXF 文件。',
    icon: DraftingCompass,
  },
  {
    title: '最终出图',
    shortTitle: '最终出图',
    caption: '整理结构化几何并输出最终 DXF',
    message: '请把当前 CAD 需求整理成明确的几何规格，优先使用结构化 DXF 工具生成最终图纸文件，并验证 DXF 可以正常解析。',
    icon: Workflow,
  },
  {
    title: '补尺寸标注',
    shortTitle: '尺寸标注',
    caption: '补全孔位、外形、槽口和注释标注',
    message: '请帮我补全这张 CAD 图的尺寸标注，包括外形尺寸、孔位、槽口、圆角、中心线和必要的文字注释。',
    icon: TerminalSquare,
  },
  {
    title: '流程/电控图',
    shortTitle: '电控流程',
    caption: '生成流程图、电气控制原理图或连接关系图',
    message: '请根据我的描述生成一张清晰的流程图或电气控制原理图，重点表达设备标签、连接关系、方向和关键注释，并输出 DXF 文件。',
    icon: Workflow,
  },
];
