"""
Groq AI Chatbot for Indian Stock Market Analysis
Provides intelligent responses to stock-related queries and comparisons
"""

from groq import Groq
import json
from typing import List, Dict, Optional


class GroqChatbot:
    """Chatbot powered by Groq AI for stock market analysis"""
    
    def __init__(self, api_key: str):
        """Initialize Groq client with API key"""
        self.client = Groq(api_key=api_key)
        self.model = "llama-3.3-70b-versatile"  # Fast and capable model (updated from deprecated 3.1)
        self.conversation_history = []
        
    def build_stock_context(self, stocks_data: List[Dict]) -> str:
        """
        Build context string from stock data for the AI
        
        Args:
            stocks_data: List of dicts containing stock metrics
            
        Returns:
            Formatted context string
        """
        if not stocks_data:
            return "No stocks currently selected."
        
        context = "Currently selected stocks:\n\n"
        
        for stock in stocks_data:
            context += f"{stock.get('Name', 'Unknown')} ({stock.get('Symbol', 'N/A')})\n"
            context += f"- Market Cap: {stock.get('Market Cap', 'N/A')}\n"
            context += f"- Current Price: ₹{stock.get('Current Price', 'N/A')}\n"
            context += f"- P/E Ratio: {stock.get('P/E Ratio', 'N/A')}\n"
            context += f"- Book Value: {stock.get('Book Value', 'N/A')}\n"
            context += f"- Price/Book: {stock.get('Price / Book', 'N/A')}\n"
            context += f"- Dividend Yield: {stock.get('Dividend Yield', 'N/A')}\n"
            context += f"- ROCE: {stock.get('ROCE', 'N/A')}\n"
            context += f"- ROE: {stock.get('ROE', 'N/A')}\n"
            context += f"- 52-Week High: ₹{stock.get('52-Week High', 'N/A')}\n"
            context += f"- 52-Week Low: ₹{stock.get('52-Week Low', 'N/A')}\n"
            context += f"- Sales YoY: {stock.get('Sales YoY %', 'N/A')}\n"
            context += f"- Net Profit YoY: {stock.get('Net Profit YoY %', 'N/A')}\n"
            context += "\n"
        
        return context
    
    def generate_response(self, user_message: str, stocks_data: Optional[List[Dict]] = None) -> str:
        """
        Generate AI response to user query
        
        Args:
            user_message: User's question
            stocks_data: Optional list of current stock data for context
            
        Returns:
            AI-generated response
        """
        try:
            # Build system prompt
            system_prompt = """You are an expert Indian stock market analyst and investment advisor. 
Your role is to help users understand stocks, compare investments, and make informed decisions.

Key guidelines:
- Focus on Indian stock market (NSE/BSE)
- Use fundamental analysis (P/E, ROE, ROCE, debt, growth rates)
- Explain concepts clearly for retail investors
- For comparisons, consider both long-term (3-5 years) and short-term (6-12 months) perspectives
- Mention risks and limitations
- Use Indian Rupees (₹) for prices
- Reference actual data when available
- Be concise but informative
- Format responses with markdown: use **bold** for emphasis, bullet points for lists, but avoid excessive formatting
- When mentioning stock names, use plain text without bold formatting

When comparing stocks:
- Long-term: Focus on ROE, ROCE, consistent profit growth, low debt, competitive advantages
- Short-term: Focus on P/E ratio, recent momentum, quarterly results, sector trends
"""
            
            # Add stock context if available
            if stocks_data:
                stock_context = self.build_stock_context(stocks_data)
                system_prompt += f"\n\nCurrent stock data available:\n{stock_context}"
            
            # Add user message to history
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })
            
            # Prepare messages for API
            messages = [
                {"role": "system", "content": system_prompt}
            ] + self.conversation_history[-10:]  # Keep last 10 messages for context
            
            # Call Groq API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=1024,
                top_p=0.9
            )
            
            # Extract response
            assistant_message = response.choices[0].message.content
            
            # Add to history
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })
            
            return assistant_message
            
        except Exception as e:
            error_msg = f"Error generating response: {str(e)}"
            print(error_msg)
            return f"Sorry, I encountered an error: {str(e)}. Please try again."
    
    def clear_history(self):
        """Clear conversation history"""
        self.conversation_history = []
    
    def get_history(self) -> List[Dict]:
        """Get conversation history"""
        return self.conversation_history


# Helper function for easy initialization
def create_chatbot(api_key: str) -> GroqChatbot:
    """Create and return a GroqChatbot instance"""
    return GroqChatbot(api_key)
