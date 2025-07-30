import React, { useState } from 'react';
import axios from 'axios';

function App() {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadResult, setUploadResult] = useState('');

  const handleFileSelect = (event) => {
    setSelectedFile(event.target.files[0]);
  };

  const handleUpload = async () => {
    if (!selectedFile) {
      alert('ファイルを選択してください');
      return;
    }

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      const response = await axios.post('http://localhost:8000/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
      setUploadResult(`アップロード成功: ${response.data.filename}`);
    } catch (error) {
      setUploadResult('アップロードに失敗しました');
      console.error('Upload error:', error);
    }
  };

  return (
    <div style={{ padding: '20px', maxWidth: '600px', margin: '0 auto' }}>
      <h1>Book Leak Detector</h1>
      <div style={{ marginBottom: '20px' }}>
        <input
          type="file"
          accept="image/*"
          onChange={handleFileSelect}
          style={{ marginBottom: '10px' }}
        />
        <br/>
        <button onClick={handleUpload} style={{ padding: '10px 20px' }}>
          画像をアップロード
        </button>
      </div>
      {uploadResult && (
        <div style={{ marginTop: '20px', padding: '10px', backgroundColor: '#f0f0f0' }}>
          {uploadResult}
        </div>
      )}
    </div>
  );
}

export default App;