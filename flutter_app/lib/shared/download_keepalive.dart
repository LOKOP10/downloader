import 'dart:io';

import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

/// 下载保活：通知原生起/停前台服务，并把**下载进度**画进常驻通知。
///
/// 为什么需要：Flutter App 退出前台后，系统会把 Dart isolate 降频甚至冻结，
/// 网络请求随之停摆 —— 表现就是「一切后台下载就断了」。前台服务让进程保持
/// 前台优先级，锁屏/切后台继续下；通知里同时给出百分比与速度，锁屏也能看进度。
///
/// 设计上是「尽力而为」：任何异常都静默吞掉，绝不让保活失败连带下载失败。
/// 桌面 / 测试环境没有这个 channel，直接跳过。
abstract final class DownloadKeepAlive {
  static const MethodChannel _channel = MethodChannel('vdl/keepalive');

  static bool _askedNotification = false;
  static bool _running = false;

  static bool get running => _running;

  /// 起服务（或更新通知文案与进度）。
  ///
  /// [percent] 0~100 显示确定进度条；null 显示不确定进度条。
  static Future<void> start(String text, {int? percent}) async {
    if (!Platform.isAndroid) return;
    try {
      if (!_askedNotification) {
        _askedNotification = true;
        // Android 13+ 前台服务的通知要用户授权；不给也不影响下载，只是看不到通知
        try {
          await Permission.notification.request();
        } catch (_) {}
      }
      await _channel.invokeMethod<bool>('start', {
        'text': text,
        'progress': percent ?? -1,
      });
      _running = true;
    } catch (_) {
      // 起不来就算了：下载本身不依赖它，只是可能被系统挂起
    }
  }

  /// 只更新通知（服务没在跑就当作 start）。
  static Future<void> update(String text, {int? percent}) =>
      start(text, percent: percent);

  /// 撤掉服务（通知一起消失）。没有在跑就什么都不做。
  static Future<void> stop() async {
    if (!Platform.isAndroid) return;
    if (!_running) return;
    _running = false;
    try {
      await _channel.invokeMethod<bool>('stop');
    } catch (_) {}
  }
}
