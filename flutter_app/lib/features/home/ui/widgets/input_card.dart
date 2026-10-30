import 'package:flutter/material.dart';
import 'package:vdl/design/colors.dart';
import 'package:vdl/design/widgets/app_card.dart';

/// 首页顶部的链接输入卡：输入框 + 粘贴 + 解析按钮。
class HomeInputCard extends StatelessWidget {
  const HomeInputCard({
    super.key,
    required this.controller,
    this.focusNode,
    required this.parsing,
    required this.onParse,
    required this.onPaste,
  });

  final TextEditingController controller;
  final FocusNode? focusNode;
  final bool parsing;
  final VoidCallback onParse;
  final VoidCallback onPaste;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('作品链接', style: TextStyle(color: palette.hint, fontSize: 13)),
          const SizedBox(height: 4),
          TextField(
            controller: controller,
            focusNode: focusNode,
            maxLines: 2,
            minLines: 1,
            style: TextStyle(color: palette.titleText, fontSize: 14),
            decoration: InputDecoration(
              isDense: true,
              border: InputBorder.none,
              contentPadding: EdgeInsets.zero,
              hintText: '粘贴抖音 / 小红书 / B站 / X 链接，禁漫可直接填车号',
              hintStyle: TextStyle(color: palette.hint, fontSize: 14),
            ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              TextButton.icon(
                onPressed: onPaste,
                icon: Icon(Icons.content_paste, size: 16, color: palette.labelText),
                label: Text('粘贴',
                    style: TextStyle(color: palette.labelText, fontSize: 13)),
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  minimumSize: const Size(0, 32),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
              const Spacer(),
              SizedBox(
                height: 36,
                child: FilledButton(
                  onPressed: parsing ? null : onParse,
                  style: FilledButton.styleFrom(
                    backgroundColor: palette.accent,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8)),
                  ),
                  child: const Text('开始解析', style: TextStyle(fontSize: 14)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
