import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  Dimensions,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Haptics from 'expo-haptics';
import { manipulateAsync, SaveFormat } from 'expo-image-manipulator';
import { extractTextFromImage, isSupported } from 'expo-text-extractor';

const SCREEN = Dimensions.get('window');
const BOX_WIDTH = Math.min(SCREEN.width - 48, 340);
const BOX_HEIGHT = 150;

function cleanPartNumber(rawText) {
  const normalized = String(rawText || '')
    .toUpperCase()
    .replace(/[^A-Z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  if (!normalized) return '';

  const candidates = normalized
    .split(' ')
    .map((value) => value.replace(/^-+|-+$/g, '').trim())
    .filter((value) => value.length >= 4 && value.length <= 30)
    .map((value) => {
      let score = 0;
      if (/[A-Z]/.test(value)) score += 3;
      if (/[0-9]/.test(value)) score += 3;
      if (/^[A-Z0-9]+$/.test(value)) score += 2;
      return { value, score };
    })
    .filter((item) => item.score >= 7)
    .sort((a, b) => b.score - a.score || b.value.length - a.value.length);

  return candidates[0]?.value || '';
}

export default function PartScannerScreen({ onClose, onDetected }) {
  const cameraRef = useRef(null);
  const scanLine = useRef(new Animated.Value(0)).current;
  const [permission, requestPermission] = useCameraPermissions();
  const [cameraReady, setCameraReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [previewLayout, setPreviewLayout] = useState({ width: SCREEN.width, height: SCREEN.height });

  useEffect(() => {
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(scanLine, {
          toValue: 1,
          duration: 1500,
          useNativeDriver: true,
        }),
        Animated.timing(scanLine, {
          toValue: 0,
          duration: 1500,
          useNativeDriver: true,
        }),
      ])
    );
    animation.start();
    return () => animation.stop();
  }, [scanLine]);

  useEffect(() => {
    if (permission && !permission.granted && permission.canAskAgain) {
      requestPermission();
    }
  }, [permission, requestPermission]);

  const lineTranslateY = useMemo(
    () =>
      scanLine.interpolate({
        inputRange: [0, 1],
        outputRange: [8, BOX_HEIGHT - 10],
      }),
    [scanLine]
  );

  const scan = async () => {
    if (busy || !cameraReady || !cameraRef.current) return;
    if (!isSupported) {
      Alert.alert('OCR Not Supported', 'Offline text recognition is not supported on this device.');
      return;
    }

    setBusy(true);
    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 1,
        skipProcessing: false,
        shutterSound: false,
      });
      if (!photo?.uri || !photo.width || !photo.height) {
        throw new Error('Camera image was not captured correctly.');
      }

      const previewRatio = previewLayout.width / previewLayout.height;
      const photoRatio = photo.width / photo.height;

      let visiblePhotoWidth = photo.width;
      let visiblePhotoHeight = photo.height;
      let offsetX = 0;
      let offsetY = 0;

      // Camera preview behaves like cover. Work out which part of the photo is visible.
      if (photoRatio > previewRatio) {
        visiblePhotoWidth = photo.height * previewRatio;
        offsetX = (photo.width - visiblePhotoWidth) / 2;
      } else if (photoRatio < previewRatio) {
        visiblePhotoHeight = photo.width / previewRatio;
        offsetY = (photo.height - visiblePhotoHeight) / 2;
      }

      const scaleX = visiblePhotoWidth / previewLayout.width;
      const scaleY = visiblePhotoHeight / previewLayout.height;

      const boxLeft = (previewLayout.width - BOX_WIDTH) / 2;
      const boxTop = (previewLayout.height - BOX_HEIGHT) / 2;

      const originX = Math.max(0, Math.round(offsetX + boxLeft * scaleX));
      const originY = Math.max(0, Math.round(offsetY + boxTop * scaleY));
      const requestedWidth = Math.max(1, Math.round(BOX_WIDTH * scaleX));
      const requestedHeight = Math.max(1, Math.round(BOX_HEIGHT * scaleY));

      const crop = {
        originX,
        originY,
        width: Math.max(1, Math.min(requestedWidth, photo.width - originX)),
        height: Math.max(1, Math.min(requestedHeight, photo.height - originY)),
      };

      const cropped = await manipulateAsync(photo.uri, [{ crop }], {
        compress: 1,
        format: SaveFormat.JPEG,
      });

      const recognizedLines = await extractTextFromImage(cropped.uri);
      const recognizedText = Array.isArray(recognizedLines)
        ? recognizedLines.join(' ')
        : String(recognizedLines || '');
      const partNumber = cleanPartNumber(recognizedText);

      if (!partNumber) {
        Alert.alert(
          'Part Number Not Detected',
          'Keep only one part number inside the scan box and try again.'
        );
        return;
      }

      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      if (typeof onDetected === 'function') onDetected(partNumber);
    } catch (error) {
      Alert.alert('Scan Failed', error?.message || 'Unable to read the part number. Please scan again.');
    } finally {
      setBusy(false);
    }
  };

  if (!permission) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color="#ffffff" />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.permissionText}>Camera permission is required to scan the part number.</Text>
        <TouchableOpacity style={styles.primaryButton} onPress={requestPermission}>
          <Text style={styles.primaryButtonText}>Allow Camera</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.secondaryButton} onPress={() => typeof onClose === 'function' && onClose()}>
          <Text style={styles.secondaryButtonText}>Close</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View
      style={styles.container}
      onLayout={(event) => setPreviewLayout(event.nativeEvent.layout)}
    >
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
        flash="off"
        mode="picture"
        onCameraReady={() => setCameraReady(true)}
      />

      <View style={styles.darkTop} />
      <View style={styles.middleRow} pointerEvents="none">
        <View style={styles.darkSide} />
        <View style={styles.scanBox}>
          <View style={[styles.corner, styles.cornerTL]} />
          <View style={[styles.corner, styles.cornerTR]} />
          <View style={[styles.corner, styles.cornerBL]} />
          <View style={[styles.corner, styles.cornerBR]} />
          <Animated.View
            style={[
              styles.scanLine,
              {
                transform: [{ translateY: lineTranslateY }],
              },
            ]}
          />
        </View>
        <View style={styles.darkSide} />
      </View>
      <View style={styles.darkBottom} />

      <TouchableOpacity style={styles.closeButton} onPress={() => typeof onClose === 'function' && onClose()} disabled={busy}>
        <Text style={styles.closeText}>×</Text>
      </TouchableOpacity>

      <View style={styles.instructionWrap}>
        <Text style={styles.instructionTitle}>Align the part number inside the box</Text>
        <Text style={styles.instructionText}>Only the text inside this box will be scanned.</Text>
      </View>

      <View style={styles.bottomBar}>
        <TouchableOpacity
          style={[styles.scanButton, (!cameraReady || busy) && styles.disabledButton]}
          onPress={scan}
          disabled={!cameraReady || busy}
        >
          {busy ? (
            <ActivityIndicator color="#ffffff" />
          ) : (
            <Text style={styles.scanButtonText}>Scan Part Number</Text>
          )}
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000000' },
  center: {
    flex: 1,
    backgroundColor: '#101418',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  permissionText: {
    color: '#ffffff',
    textAlign: 'center',
    fontSize: 16,
    lineHeight: 23,
    marginBottom: 20,
  },
  primaryButton: {
    width: '100%',
    backgroundColor: '#176b43',
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: 'center',
    marginBottom: 12,
  },
  primaryButtonText: { color: '#ffffff', fontSize: 16, fontWeight: '800' },
  secondaryButton: {
    width: '100%',
    borderWidth: 1,
    borderColor: '#ffffff66',
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: 'center',
  },
  secondaryButtonText: { color: '#ffffff', fontSize: 16, fontWeight: '700' },
  darkTop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.58)' },
  middleRow: { height: BOX_HEIGHT, flexDirection: 'row' },
  darkSide: { flex: 1, backgroundColor: 'rgba(0,0,0,0.58)' },
  scanBox: {
    width: BOX_WIDTH,
    height: BOX_HEIGHT,
    overflow: 'hidden',
  },
  darkBottom: { flex: 1, backgroundColor: 'rgba(0,0,0,0.58)' },
  corner: {
    position: 'absolute',
    width: 30,
    height: 30,
    borderColor: '#38c172',
    zIndex: 3,
  },
  cornerTL: { left: 0, top: 0, borderLeftWidth: 4, borderTopWidth: 4 },
  cornerTR: { right: 0, top: 0, borderRightWidth: 4, borderTopWidth: 4 },
  cornerBL: { left: 0, bottom: 0, borderLeftWidth: 4, borderBottomWidth: 4 },
  cornerBR: { right: 0, bottom: 0, borderRightWidth: 4, borderBottomWidth: 4 },
  scanLine: {
    position: 'absolute',
    left: 10,
    right: 10,
    height: 2,
    backgroundColor: '#63ff9c',
    shadowColor: '#63ff9c',
    shadowOpacity: 0.9,
    shadowRadius: 8,
    elevation: 6,
  },
  closeButton: {
    position: 'absolute',
    top: 18,
    left: 18,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeText: { color: '#ffffff', fontSize: 30, lineHeight: 32 },
  instructionWrap: {
    position: 'absolute',
    left: 20,
    right: 20,
    bottom: 118,
    alignItems: 'center',
  },
  instructionTitle: { color: '#ffffff', fontSize: 16, fontWeight: '800', textAlign: 'center' },
  instructionText: { color: '#d6dde0', fontSize: 13, marginTop: 5, textAlign: 'center' },
  bottomBar: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 24,
    backgroundColor: 'rgba(0,0,0,0.70)',
  },
  scanButton: {
    minHeight: 54,
    borderRadius: 14,
    backgroundColor: '#176b43',
    alignItems: 'center',
    justifyContent: 'center',
  },
  disabledButton: { opacity: 0.55 },
  scanButtonText: { color: '#ffffff', fontSize: 16, fontWeight: '900' },
});
