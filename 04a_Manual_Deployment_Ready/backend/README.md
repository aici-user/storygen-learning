# StoryGen Backend

This backend provides AI-powered story generation using Google's Agent Development Kit (ADK) with WebSocket support for real-time streaming.

## Features

- **ADK Integration**: Uses Google's Agent Development Kit with Gemini models
- **WebSocket Support**: Real-time bidirectional communication
- **Story Generation**: AI-powered creative story generation based on keywords
- **Session Management**: Proper ADK session handling and cleanup
- **CORS Support**: Configured for frontend integration

## Setup

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Set SSL Certificate (Required for ADK)

```bash
export SSL_CERT_FILE=$(python -m certifi)
```

### 3. Configure Environment

Create a `.env` file in the backend directory:

```env
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_API_KEY=your_actual_google_api_key_here
```

**To get your API key:**
1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Click "Get API key" 
3. Create a new project or select an existing one
4. Generate and copy your API key
5. Replace `your_actual_google_api_key_here` in the `.env` file

### 4. Run the Server

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The server will start at `http://localhost:8000`

## API Endpoints

### WebSocket

- **URL**: `ws://localhost:8000/ws/{user_id}`
- **Purpose**: Real-time story generation

#### Message Format

**Client to Server:**
```json
{
  "type": "generate_story",
  "data": "keywords separated by spaces"
}
```

**Server to Client:**
```json
{
  "type": "story_chunk",
  "data": "partial story text",
  "partial": true
}
```

```json
{
  "type": "turn_complete",
  "turn_complete": true,
  "interrupted": false
}
```

### HTTP Endpoints

- **GET /**: API information
- **GET /health**: Health check

## Architecture

```
├── main.py                 # FastAPI server with WebSocket endpoints
├── story_agent/
│   ├── __init__.py
│   └── agent.py           # ADK story generation agent
├── requirements.txt        # Python dependencies
└── README.md              # This file
```

## Agent Configuration

The story agent is configured with:
- **Model**: `gemini-3.8-flash`
- **Purpose**: Creative story generation
- **Input**: Keywords/themes from users
- **Output**: 200-400 word creative stories
- **Features**: Vivid descriptions, character development, plot structure

## Development

### Testing the WebSocket

You can test the WebSocket connection using a simple JavaScript client:

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/test_user');

ws.onopen = () => {
  console.log('Connected');
  ws.send(JSON.stringify({
    type: 'generate_story',
    data: 'robot detective mystery'
  }));
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  console.log('Received:', message);
};
```

### Logs

The server provides detailed logging for:
- Client connections/disconnections
- Agent session management
- Message routing
- Error handling

## Troubleshooting

### Common Issues

1. **API Key Error**: Ensure your Google API key is correctly set in `.env`
2. **SSL Certificate Error**: Run `export SSL_CERT_FILE=$(python -m certifi)`
3. **Model Not Available**: Use the replacement model named in the error message and update `model=` in `agent.py`
4. **WebSocket Connection Issues**: Check CORS settings and port availability

### Dependencies

- Python 3.8+
- Google ADK 1.2.1
- FastAPI 0.115.0
- Valid Google AI Studio API key

## Production Considerations

For production deployment:

1. **Scalability**: Use multiple server instances with load balancing
2. **Session Storage**: Replace `InMemorySessionService` with persistent storage
3. **Security**: Implement authentication and rate limiting
4. **Monitoring**: Add comprehensive logging and health checks
5. **SSL/TLS**: Use HTTPS/WSS in production 